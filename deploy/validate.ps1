$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is required to validate and start the production stack."
}

foreach ($envFile in @(".env.caddy", ".env.app", ".env.grafana", ".env.alerting")) {
    if (-not (Test-Path $envFile)) {
        throw "Copy $envFile.example to $envFile and replace all placeholders before validation."
    }
    $envContent = Get-Content $envFile -Raw
    foreach ($placeholder in @("replace-with", "YOUR_PROJECT", "change-me", "docqa.example.com", "alerts.example.com")) {
        if ($envContent -match [regex]::Escape($placeholder)) {
            throw "Production environment '$envFile' still contains placeholder '$placeholder'."
        }
    }
}

docker compose config --quiet

docker compose up -d --build

$deadline = (Get-Date).AddSeconds(180)
do {
    Start-Sleep -Seconds 5
    $ready = docker compose exec -T app curl -fsS http://127.0.0.1:8000/health/ready 2>$null
} while ($LASTEXITCODE -ne 0 -and (Get-Date) -lt $deadline)

if ($LASTEXITCODE -ne 0) {
    throw "DocQA application did not become ready."
}

try {
    $promTargets = Invoke-RestMethod -Uri "http://127.0.0.1:9090/api/v1/targets" -Method Get
    $docqaTarget = @($promTargets.data.activeTargets | Where-Object {
        $_.labels.job -eq "docqa"
    })
    if (-not $docqaTarget -or $docqaTarget[0].health -ne "up") {
        throw "Prometheus has no healthy active DocQA target."
    }
} catch {
    throw "Prometheus target validation failed: $($_.Exception.Message)"
}

try {
    $grafanaHealth = Invoke-RestMethod -Uri "http://127.0.0.1:3000/api/health" -Method Get
    if ($grafanaHealth.database -ne "ok") {
        throw "Grafana database health is not ok."
    }
} catch {
    throw "Grafana health validation failed: $($_.Exception.Message)"
}

$alert = @(
    @{
        labels = @{
            alertname = "DocQADeploymentDeliveryTest"
            severity = "info"
        }
        annotations = @{
            summary = "DocQA alert delivery validation"
            description = "Test alert sent through Alertmanager."
        }
        startsAt = (Get-Date).ToUniversalTime().ToString("o")
    }
)

$body = $alert | ConvertTo-Json -Depth 5
$result = Invoke-RestMethod -Uri "http://127.0.0.1:9093/api/v2/alerts" `
    -Method Post -ContentType "application/json" -Body $body

try {
    $alertmanagerHealth = Invoke-RestMethod -Uri "http://127.0.0.1:9093/-/healthy" -Method Get
} catch {
    throw "Alertmanager health validation failed: $($_.Exception.Message)"
}

Write-Output "Stack is ready. Alertmanager accepted the test alert API call."
Write-Output "Verify externally that the configured human notification webhook received the alert."
