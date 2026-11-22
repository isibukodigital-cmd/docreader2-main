$ErrorActionPreference = "Stop"

$databaseUrl = $env:DOCQA_RESTORE_DATABASE_URL
if (-not $databaseUrl) {
    throw "Set DOCQA_RESTORE_DATABASE_URL to a disposable restored/recovery database."
}

try {
    $databaseUri = [System.Uri]$databaseUrl
} catch {
    throw "DOCQA_RESTORE_DATABASE_URL must be a PostgreSQL connection URI."
}

$hostName = $databaseUri.Host.ToLowerInvariant()
if (@("localhost", "127.0.0.1", "::1") -notcontains $hostName) {
    throw "Restore integration refuses a non-local recovery host. Run against a dedicated restored instance only."
}

$psql = Get-Command psql -ErrorAction SilentlyContinue
if (-not $psql) {
    throw "psql was not found. Install PostgreSQL client tools on the restore test host."
}

$root = Split-Path -Parent $PSScriptRoot
$standins = Join-Path $root "tests\sql\ci_supabase_standins.sql"
$migrations = Get-ChildItem (Join-Path $root "supabase\migrations") -Filter "*.sql" |
    Sort-Object Name

Write-Output "Validating restored database against Supabase-compatible stand-ins"
& $psql.Source -X --set=ON_ERROR_STOP=1 "--dbname=$databaseUrl" "--file=$standins"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

foreach ($migration in $migrations) {
    Write-Output "Applying $($migration.Name)"
    & $psql.Source -X --set=ON_ERROR_STOP=1 "--dbname=$databaseUrl" "--file=$($migration.FullName)"
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$integration = Join-Path $root "tests\sql\integration.sql"
Write-Output "Running restore recovery assertions"
& $psql.Source -X --set=ON_ERROR_STOP=1 "--dbname=$databaseUrl" "--file=$integration"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Output "Restore integration test passed."
