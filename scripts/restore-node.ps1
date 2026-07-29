[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $BackupDirectory,
    [Parameter(Mandatory)]
    [string] $ConfirmBackupId,
    [switch] $RecoveryMaterialInstalled
)

$ErrorActionPreference = "Stop"
$utf8 = [Text.UTF8Encoding]::new($false)
if (-not $RecoveryMaterialInstalled) {
    throw "Install the matching protected secrets and pass -RecoveryMaterialInstalled"
}

$root = Split-Path -Parent $PSScriptRoot
$backup = (Resolve-Path -LiteralPath $BackupDirectory).Path
$project = if ($env:COMPOSE_PROJECT_NAME) { $env:COMPOSE_PROJECT_NAME } else { "cooperative-clearing" }
$compose = @("compose", "--project-directory", $root, "-f", (Join-Path $root "compose.yaml"))
foreach ($name in @("COMPLETE", "SHA256SUMS", "manifest.env", "database.dump", "blobs.tar.gz")) {
    if (-not (Test-Path -LiteralPath (Join-Path $backup $name))) {
        throw "Incomplete backup: missing $name"
    }
}
$manifest = @{}
Get-Content (Join-Path $backup "manifest.env") | ForEach-Object {
    $parts = $_ -split "=", 2
    if ($parts.Count -eq 2) { $manifest[$parts[0]] = $parts[1] }
}
if ($manifest.backup_id -ne $ConfirmBackupId) {
    throw "ConfirmBackupId must exactly match $($manifest.backup_id)"
}
if (
    $manifest.backup_kind -eq "FULL" -and
    -not (Test-Path -LiteralPath (Join-Path $backup "recovery.bundle.enc"))
) {
    throw "FULL backup is missing its encrypted recovery bundle"
}
if (
    $manifest.backup_kind -eq "FULL" -and
    $manifest.release_material -ne "included-verified"
) {
    throw "FULL backup is missing its verified release bundle"
}
if ($manifest.release_material -eq "included-verified") {
    if (-not $env:COOP_RELEASE_PUBLIC_KEY) {
        throw "COOP_RELEASE_PUBLIC_KEY is required to restore the signed release"
    }
    $releaseDirectory = Join-Path $backup "release"
    $verification = @(
        (Join-Path $PSScriptRoot "release_bundle.py")
        "verify"
        "--bundle"
        $releaseDirectory
        "--public-key"
        $env:COOP_RELEASE_PUBLIC_KEY
        "--expected-release"
        $manifest.release
        "--load-images"
    )
    if ($env:COOP_RELEASE_LICENSE_POLICY_SHA256) {
        $verification += @(
            "--expected-policy-sha256",
            $env:COOP_RELEASE_LICENSE_POLICY_SHA256
        )
    }
    & python @verification
    if ($LASTEXITCODE -ne 0) { throw "Recovery release verification failed" }
    $installedComposeHash = (
        Get-FileHash -LiteralPath (Join-Path $root "compose.yaml") -Algorithm SHA256
    ).Hash
    $releaseComposeHash = (
        Get-FileHash -LiteralPath (Join-Path $releaseDirectory "node/compose.yaml") -Algorithm SHA256
    ).Hash
    if ($installedComposeHash -ne $releaseComposeHash) {
        throw "Installed node payload does not match the recovery release"
    }
}

Get-Content (Join-Path $backup "SHA256SUMS") | ForEach-Object {
    $parts = $_ -split "  ", 2
    $actual = (Get-FileHash -LiteralPath (Join-Path $backup $parts[1]) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $parts[0]) { throw "Checksum mismatch: $($parts[1])" }
}
& docker run --rm -v "$($backup):/backup:ro" postgres:18-alpine sh -ec "pg_restore --list /backup/database.dump >/dev/null; tar -tzf /backup/blobs.tar.gz >/dev/null"
if ($LASTEXITCODE -ne 0) { throw "Backup archive validation failed" }

$envFile = Join-Path $root ".env"
$lines = if (Test-Path $envFile) { @(Get-Content $envFile) } else { @() }
$foundRelease = $false
$lines = $lines | ForEach-Object {
    if ($_ -match "^COOP_RELEASE=") {
        $foundRelease = $true
        "COOP_RELEASE=$($manifest.release)"
    }
    else { $_ }
}
if (-not $foundRelease) { $lines += "COOP_RELEASE=$($manifest.release)" }
[IO.File]::WriteAllLines($envFile, [string[]] $lines, $utf8)
$env:COOP_RELEASE = $manifest.release

& docker @compose stop gateway api worker frontend | Out-Null
& docker @compose up -d postgres | Out-Null
& docker @compose exec -T postgres dropdb -U coop_migrator --if-exists --force cooperative_clearing
if ($LASTEXITCODE -ne 0) { throw "Database drop failed" }
& docker @compose exec -T postgres createdb -U coop_migrator -O coop_migrator cooperative_clearing
if ($LASTEXITCODE -ne 0) { throw "Database creation failed" }
$restoreArguments = @($compose) + @(
    "exec", "-T", "postgres", "pg_restore",
    "-U", "coop_migrator", "-d", "cooperative_clearing",
    "--exit-on-error", "--no-owner"
)
$restoreProcess = Start-Process -FilePath "docker" -ArgumentList $restoreArguments -NoNewWindow -Wait -PassThru -RedirectStandardInput (Join-Path $backup "database.dump")
if ($restoreProcess.ExitCode -ne 0) { throw "Database restore failed" }
& docker run --rm -v "$($project)_blob-data:/target" -v "$($backup):/backup:ro" postgres:18-alpine sh -ec "find /target -mindepth 1 -delete; tar -C /target -xzf /backup/blobs.tar.gz"
if ($LASTEXITCODE -ne 0) { throw "Blob restore failed" }

& docker @compose run --rm migrate
if ($LASTEXITCODE -ne 0) { throw "Migration failed" }
& docker @compose run --rm init-node
if ($LASTEXITCODE -ne 0) { throw "Node initialization check failed" }
& docker @compose run --rm bootstrap-identity
if ($LASTEXITCODE -ne 0) { throw "Identity bootstrap check failed" }
& docker @compose run --rm --no-deps api coopctl verify-restore-consistency
if ($LASTEXITCODE -ne 0) { throw "Restored data and key consistency verification failed" }
& docker @compose run --rm --no-deps api coopctl verify-journal
if ($LASTEXITCODE -ne 0) { throw "Journal verification failed" }
& docker @compose up -d api worker frontend gateway
if ($LASTEXITCODE -ne 0) { throw "Runtime startup failed" }
$httpPort = if ($env:COOP_HTTP_PORT) { $env:COOP_HTTP_PORT } else { $null }
if (-not $httpPort) {
    $portLine = Get-Content $envFile |
        Where-Object { $_ -match "^COOP_HTTP_PORT=" } |
        Select-Object -Last 1
    $httpPort = if ($portLine) {
        $portLine.Substring("COOP_HTTP_PORT=".Length)
    } else { "8080" }
}
& (Join-Path $PSScriptRoot "verify-stack.ps1") -BaseUrl "http://127.0.0.1:$httpPort"

Write-Host "Restore completed from $($manifest.backup_id)"
