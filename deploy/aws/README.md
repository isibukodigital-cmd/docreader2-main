# AWS deployment

This Terraform configuration targets `eu-north-1` and creates a public
Application Load Balancer, private EC2 application instances, private RDS
PostgreSQL, Cognito, ECR, and a two-AZ VPC. It uses SSM instead of SSH. It does
not create AWS resources until an operator runs `terraform apply`.

## Cost and prerequisites

This is not guaranteed to fit an AWS free tier or free-trial credit. The ALB,
RDS instance, and two NAT gateways incur ongoing charges; RDS Multi-AZ adds
cost. The app Auto Scaling group defaults to zero instances until an image has
been pushed and the schema installed. RDS deletion protection is enabled by
default, and a final snapshot is retained when the database is destroyed.

Before applying, confirm the account, region, IAM Identity Center permission
set, service quotas, and budget. Create three OAuth applications and configure
these Terraform inputs:

- Google web OAuth client ID and secret.
- Microsoft Entra confidential OIDC client ID and secret for Cognito federation.
  Configure supported account types for work/school and personal accounts.
- A separate Microsoft Entra **SPA** client ID for Graph authorization.
  Configure delegated `User.Read`, `Group.Read.All`, and
  `RoleManagement.Read.Directory` permissions and tenant consent.

The Cognito domain includes a generated suffix, so its external-provider
callback is available after the first infrastructure apply. Register
`<cognito_domain>/oauth2/idpresponse` in the Google and Microsoft confidential
OIDC clients before testing federation. The Microsoft provider uses the
`common` authority to support organizational and personal accounts. Register
the application domain as the separate Graph SPA redirect URI.

Cognito OIDC provider client secrets are stored in Terraform state. Do not enter
them until the versioned, encrypted S3 state backend below is initialized. Do
not commit `terraform.tfvars`, state, or plan files.

For production HTTPS, provide a Route 53 hosted-zone ID and an application
domain that resolves in that zone. Terraform requests and validates an ACM
certificate, creates the DNS record, and registers the HTTPS callback and
logout URLs with Cognito. Without a domain, the ALB exposes HTTP for
infrastructure testing and Cognito only allows the local callback; federated
sign-in is not ready for deployment.

## Prepare state and inspect the plan

Use an authenticated AWS CLI/IAM Identity Center profile with permission to
create the resources in the plan. AWS CLI, Terraform 1.10+, Docker, and the
Session Manager plugin are required on the operator workstation. The app role
uses SSM and has no SSH ingress.

First create the encrypted, versioned state bucket. This bootstrap stack uses
local state, which contains no application credentials:

```powershell
cd deploy/aws/terraform-state
terraform init
terraform validate
terraform plan
terraform apply
$stateBucket = terraform output -raw state_bucket_name
```

Then configure the main stack's S3 backend and inputs:

```powershell
cd ../terraform
Copy-Item terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with the domain, OAuth IDs, and local secret values.
terraform init `
  -backend-config="bucket=$stateBucket" `
  -backend-config="key=docreader/production/terraform.tfstate" `
  -backend-config="region=eu-north-1" `
  -backend-config="encrypt=true" `
  -backend-config="use_lockfile=true"
terraform fmt -check -recursive
terraform validate
terraform plan -out=aws.plan
```

Review the plan and monthly costs before applying. The main state backend uses
S3 native locking and bucket versioning. Restrict its IAM access to trusted
operators; the state contains Cognito OIDC secrets and infrastructure metadata.
For a disposable environment, set both `db_deletion_protection` and
`cognito_deletion_protection` to `false` only after deciding how to retain or
delete the data.

## Build, initialize, and start the application

The first apply leaves the Auto Scaling group at zero, but still creates
billable network, ALB, RDS, and identity resources. After the ECR repository is
created, build and push an immutable image tag:

```powershell
$region = "eu-north-1"
$repository = terraform output -raw ecr_repository_url
$accountId = (aws sts get-caller-identity --query Account --output text)
$registry = $repository.Split("/")[0]
$tag = "release-" + (Get-Date -Format "yyyyMMddHHmmss")
$projectRoot = (Resolve-Path ..\..\..).Path
aws ecr get-login-password --region $region |
  docker login --username AWS --password-stdin $registry
docker build --platform linux/amd64 -f "$projectRoot\Dockerfile" -t "${repository}:${tag}" $projectRoot
docker push "${repository}:${tag}"
```

Set `app_image_tag` to the pushed tag and `app_desired_capacity` to `1` in
`terraform.tfvars`, then run `terraform apply`. The private instance starts the
container and reads the RDS-managed password from Secrets Manager. The ALB
doesn't route traffic to it until `/health/ready` succeeds. The app image
includes the versioned SQL migrations and `scripts/setup_database.py`.

The database is private. Before the first app becomes healthy, use SSM Run
Command on the tagged app instance to initialize it:

```powershell
aws ssm send-command `
  --targets "Key=tag:Name,Values=docreader-app" `
  --document-name "AWS-RunShellScript" `
  --parameters 'commands=["docker exec docreader-app python /app/scripts/setup_database.py --provider postgres --yes"]'
```

Check the command's invocation status; after a successful initialization, the
app should pass its ALB health check. For later schema releases, run the setup
script from the newly pushed image in a one-off Docker container through SSM
before updating the Auto Scaling group to that image. The script applies only
pending versions and refuses an untracked pre-existing DocReader schema.

If `app_domain_name` is configured, use the HTTPS URL in the Cognito callback
and logout settings. Register the Cognito Hosted UI `/oauth2/idpresponse` URL
with the Google and Microsoft OIDC applications. Register the application
origin as the redirect URI of the separate Microsoft Graph SPA application.
Copy the Terraform outputs for the pool ID, public client ID, and Cognito domain
into any non-AWS deployment that uses this same user pool; this stack injects
them into the EC2 app automatically.

## Updating or removing the deployment

For an application release, build and push a new `release-*` tag, update
`app_image_tag`, and apply. The Auto Scaling group refreshes instances from the
new immutable image. Run the new image's migration command through SSM before
switching application instances when a release includes schema changes.

Before teardown, take and verify an RDS snapshot. Disable DB and Cognito
deletion protection in `terraform.tfvars` and apply that change first. Then
review and apply a destroy plan, and explicitly account for the final RDS
snapshot and versioned Terraform state bucket; neither is removed automatically
by this stack.
