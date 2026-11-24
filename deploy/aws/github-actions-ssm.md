# GitHub Actions deployment through AWS Systems Manager

The `deploy` job in `.github/workflows/ci.yml` runs only after the full `test`
job passes, and only for pushes to `main`. It builds an immutable image tagged
with the commit SHA, pushes it to ECR, and asks the configured EC2 instance to
deploy it through SSM Run Command. The script waits for `/health/ready` via the
Compose health check and restores the previous image if the new one fails.

The rollout replaces the app container before checking it, so a failed release
can cause a short interruption while the previous image is restored. It does
not modify `.env.app`, `.env.caddy`, `.env.grafana`, or `.env.alerting`.
Database migrations are not run automatically; apply and verify schema changes
through a separate, reviewed release step.

## 1. Prepare EC2 and ECR

The EC2 instance must be online in Systems Manager and have an instance role
with `AmazonSSMManagedInstanceCore`. Add
`AmazonEC2ContainerRegistryReadOnly` to that same role so the host can pull
release images. The instance must have outbound HTTPS access to ECR and SSM.

Create a private ECR repository in `eu-north-1`, for example `docreader2-app`.
Enable immutable tags. Keep several recent `git-*` tags so a prior release can
be restored.

## 2. Configure GitHub OIDC

In IAM, create an identity provider for `token.actions.githubusercontent.com`
if the AWS account does not already have one:

- Audience: `sts.amazonaws.com`
- Subject: `repo:isibukodigital-cmd/docreader2-main:ref:refs/heads/main`

Create an IAM role trusted only by that provider and subject. Attach a
least-privilege policy with:

- `ecr:GetAuthorizationToken` on `*`.
- `ecr:BatchCheckLayerAvailability`, `ecr:CompleteLayerUpload`,
  `ecr:InitiateLayerUpload`, `ecr:PutImage`, and `ecr:UploadLayerPart` on the
  selected ECR repository ARN.
- `ssm:SendCommand` on the selected EC2 instance ARN and the AWS-owned
  `AWS-RunShellScript` document ARN for `eu-north-1`.
- `ssm:GetCommandInvocation` on `*`.

Do not create long-lived AWS access keys for GitHub Actions. Restrict the role
trust policy to this repository and the `main` branch.

The trust relationship should use this condition (replace the account ID and
role name as appropriate):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": "repo:isibukodigital-cmd/docreader2-main:ref:refs/heads/main"
      }
    }
  }]
}
```

For the permissions policy, replace `ACCOUNT_ID`, `INSTANCE_ID`, and
`ECR_REPOSITORY` with your values:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:CompleteLayerUpload",
        "ecr:InitiateLayerUpload",
        "ecr:PutImage",
        "ecr:UploadLayerPart"
      ],
      "Resource": "arn:aws:ecr:eu-north-1:ACCOUNT_ID:repository/ECR_REPOSITORY"
    },
    {
      "Effect": "Allow",
      "Action": "ssm:SendCommand",
      "Resource": [
        "arn:aws:ec2:eu-north-1:ACCOUNT_ID:instance/INSTANCE_ID",
        "arn:aws:ssm:eu-north-1::document/AWS-RunShellScript"
      ]
    },
    {
      "Effect": "Allow",
      "Action": "ssm:GetCommandInvocation",
      "Resource": "*"
    }
  ]
}
```

## 3. Add repository variables

In GitHub, open **Settings → Secrets and variables → Actions → Variables** and
add:

| Variable | Example |
| --- | --- |
| `AWS_REGION` | `eu-north-1` |
| `AWS_ACCOUNT_ID` | Your 12-digit AWS account ID |
| `AWS_ROLE_ARN` | ARN of the OIDC deployment role |
| `ECR_REPOSITORY` | `docreader2-app` |
| `EC2_INSTANCE_ID` | ID of the SSM-managed app instance |

These are identifiers, not credentials. The workflow does not need your
application `.env` values.

## 4. Update EC2 Compose once

The app service in `deploy/docker-compose.yml` now accepts an image override:

```yaml
  app:
    image: ${DOCQA_IMAGE:-docreader2-app:local}
    build:
      context: ..
      dockerfile: Dockerfile
```

Copy this Compose change to `~/docreader2-main/deploy/docker-compose.yml` on
EC2, preserving the existing env files and the `grafana_access` network change.
Validate it with `docker compose config --quiet`. Ensure
`/home/ubuntu/docreader2-main/deploy` is the path used on the instance, or
adjust that path in the workflow.

## 5. Enable automatic releases

Commit and push the workflow, Compose, app, and test changes to `main`. A
successful CI run on that branch will then publish and deploy that exact commit.
If CI fails, deployment is skipped. If the deployed image fails its health
check, the SSM script restores the previously running image and marks the
workflow failed.

For production, protect `main` with required CI checks and consider requiring
approval for the GitHub `production` environment before the deploy job runs.
