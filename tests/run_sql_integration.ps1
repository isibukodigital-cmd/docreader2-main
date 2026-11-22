$ErrorActionPreference = "Stop"

$databaseUrl = $env:DOCQA_TEST_DATABASE_URL
if (-not $databaseUrl) {
    throw "Set DOCQA_TEST_DATABASE_URL to a disposable local Supabase/Postgres database."
}

try {
    $databaseUri = [System.Uri]$databaseUrl
} catch {
    throw "DOCQA_TEST_DATABASE_URL must be a PostgreSQL connection URI."
}

if (@("localhost", "127.0.0.1", "::1") -notcontains $databaseUri.Host.ToLowerInvariant()) {
    throw "Refusing to apply test migrations to a non-local database host."
}

$psql = Get-Command psql -ErrorAction SilentlyContinue
if (-not $psql) {
    throw "psql was not found. Start a local Supabase/Postgres instance and install the PostgreSQL client."
}

$root = Split-Path -Parent $PSScriptRoot
$migrationDirectory = Join-Path $root "supabase\migrations"
$migrations = Get-ChildItem $migrationDirectory -Filter "*.sql" |
    Sort-Object Name

foreach ($migration in $migrations) {
    Write-Output "Applying $($migration.Name)"
    & $psql.Source -X --set=ON_ERROR_STOP=1 "--dbname=$databaseUrl" "--file=$($migration.FullName)"
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$testFile = Join-Path $PSScriptRoot "sql\integration.sql"
Write-Output "Running SQL integration assertions"
& $psql.Source -X --set=ON_ERROR_STOP=1 "--dbname=$databaseUrl" "--file=$testFile"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Output "SQL integration assertions passed."
