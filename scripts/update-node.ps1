[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern("^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")]
    [string] $TargetRelease,
    [string] $OfflineBundle,
    [switch] $Build,
    [switch] $AllowDataOnlyBackup
)

$ErrorActionPreference = "Stop"
$utf8 = [Text.UTF8Encoding]::new($false)
$root = Split-Path -Parent $PSScriptRoot
function Get-RuntimeSetting([string] $Name) {
    $output = @(& python (Join-Path $PSScriptRoot "runtime_environment.py") get --root $root --name $Name)
    if ($LASTEXITCODE -ne 0) { throw "Runtime setting resolution failed: $Name" }
    if ($output.Count -eq 0) { return "" }
    return $output[-1].Trim()
}
$releasePublicKey = if ($env:COOP_RELEASE_PUBLIC_KEY) {
    $env:COOP_RELEASE_PUBLIC_KEY
} else { Get-RuntimeSetting "COOP_RELEASE_PUBLIC_KEY" }
$policySha256 = if ($env:COOP_RELEASE_LICENSE_POLICY_SHA256) {
    $env:COOP_RELEASE_LICENSE_POLICY_SHA256
} else { Get-RuntimeSetting "COOP_RELEASE_LICENSE_POLICY_SHA256" }
$currentVerifiedBundle = if ($env:COOP_VERIFIED_RELEASE_BUNDLE) {
    $env:COOP_VERIFIED_RELEASE_BUNDLE
} else { Get-RuntimeSetting "COOP_VERIFIED_RELEASE_BUNDLE" }
$compose = @("compose", "--project-directory", $root, "-f", (Join-Path $root "compose.yaml"))
function Get-DatabaseSchema {
    $output = @(& docker @compose run --rm --no-deps api alembic current)
    if ($LASTEXITCODE -ne 0) { throw "Database schema resolution failed" }
    $matches = @($output | Where-Object { $_ -match "^([0-9A-Za-z][0-9A-Za-z_-]{0,127})(?: \(head\))?$" })
    if ($matches.Count -ne 1) { throw "Cannot determine one current database schema revision" }
    [void] ($matches[0] -match "^([0-9A-Za-z][0-9A-Za-z_-]{0,127})(?: \(head\))?$")
    return $Matches[1]
}
$envFile = Join-Path $root ".env"
$current = if ($env:COOP_RELEASE) { $env:COOP_RELEASE } else { "0.1.0-dev" }
if (Test-Path $envFile) {
    $stored = Get-Content $envFile | Where-Object { $_ -match "^COOP_RELEASE=" } | Select-Object -Last 1
    if ($stored) { $current = $stored.Substring("COOP_RELEASE=".Length) }
}
if ($current -eq $TargetRelease) { throw "Release $TargetRelease is already selected" }
$environmentOutput = & python (Join-Path $PSScriptRoot "runtime_environment.py") resolve --root $root
if ($LASTEXITCODE -ne 0) { throw "Runtime environment resolution failed" }
$environment = ($environmentOutput | Select-Object -Last 1).Trim()
$failpoint = if ($env:COOP_UPDATE_FAILPOINT) {
    $env:COOP_UPDATE_FAILPOINT
} else { "none" }
if ($failpoint -notin @("none", "after-release-switch", "after-migration", "after-startup")) {
    throw "Unsupported COOP_UPDATE_FAILPOINT: $failpoint"
}
if ($environment -eq "production" -and $failpoint -ne "none") {
    throw "Update faultpoints are forbidden in production"
}
if ($environment -eq "production" -and -not $OfflineBundle) {
    throw "Production update requires a signed offline bundle"
}
if ($environment -eq "production" -and $Build) {
    throw "Production update cannot build images from source"
}
if ($environment -eq "production" -and (-not $releasePublicKey -or -not $policySha256)) {
    throw "Production update requires the persisted release public key and license-policy SHA-256"
}
if ($environment -eq "production" -and -not $currentVerifiedBundle) {
    throw "Production update requires the verified current release bundle for rollback"
}
$httpPort = if ($env:COOP_HTTP_PORT) { $env:COOP_HTTP_PORT } else { $null }
if (-not $httpPort -and (Test-Path $envFile)) {
    $portLine = Get-Content $envFile |
        Where-Object { $_ -match "^COOP_HTTP_PORT=" } |
        Select-Object -Last 1
    if ($portLine) { $httpPort = $portLine.Substring("COOP_HTTP_PORT=".Length) }
}
if (-not $httpPort) { $httpPort = "8080" }

$currentSchema = Get-DatabaseSchema
$targetSchema = $currentSchema
$bundle = ""
if ($OfflineBundle) {
    $bundle = (Resolve-Path -LiteralPath $OfflineBundle).Path
    if (-not $releasePublicKey) {
        throw "COOP_RELEASE_PUBLIC_KEY must name the independently provisioned public key"
    }
    if (-not $currentVerifiedBundle) {
        throw "Signed update requires the verified current release bundle for rollback"
    }
    $currentVerifiedBundle = (Resolve-Path -LiteralPath $currentVerifiedBundle).Path
    $previousVerification = @(
        (Join-Path $PSScriptRoot "release_bundle.py")
        "verify"
        "--bundle"
        $currentVerifiedBundle
        "--public-key"
        $releasePublicKey
        "--expected-release"
        $current
    )
    $verification = @(
        (Join-Path $PSScriptRoot "release_bundle.py")
        "verify"
        "--bundle"
        $bundle
        "--public-key"
        $releasePublicKey
        "--expected-release"
        $TargetRelease
        "--installed-release"
        $current
        "--installed-schema"
        $currentSchema
        "--load-images"
    )
    if ($policySha256) {
        $previousVerification += @("--expected-policy-sha256", $policySha256)
        $verification += @("--expected-policy-sha256", $policySha256)
    }
    & python @previousVerification | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Current release bundle verification failed" }
    $verificationOutput = @(& python @verification)
    if ($LASTEXITCODE -ne 0) { throw "Offline bundle verification failed" }
    $verificationResult = $verificationOutput[-1] | ConvertFrom-Json
    $targetSchema = [string] $verificationResult.database_schema_revision
    if ($targetSchema -notmatch "^[0-9A-Za-z][0-9A-Za-z_-]{0,127}$") {
        throw "Verified release returned an invalid target schema revision"
    }
    $verificationOutput | Write-Output
}
$env:COOP_BACKUP_VERIFIER_RELEASE = $TargetRelease
$backupArgs = @{}
if ($env:COOP_BACKUP_ROOT) { $backupArgs.BackupRoot = $env:COOP_BACKUP_ROOT }
if ($env:COOP_ENCRYPTED_RECOVERY_BUNDLE) {
    $backupArgs.EncryptedRecoveryBundle = $env:COOP_ENCRYPTED_RECOVERY_BUNDLE
}
if ($currentVerifiedBundle) {
    $backupArgs.VerifiedReleaseBundle = $currentVerifiedBundle
}
$backup = & (Join-Path $PSScriptRoot "backup-node.ps1") @backupArgs | Select-Object -Last 1
$kindLine = Get-Content (Join-Path $backup "manifest.env") | Where-Object { $_ -match "^backup_kind=" }
$kind = $kindLine.Substring("backup_kind=".Length)
if ($kind -ne "FULL") {
    if ($environment -eq "production" -or -not $AllowDataOnlyBackup) {
        throw "Update refused: pre-update backup is DATA_ONLY"
    }
}

$operations = Join-Path $root ".operations"
New-Item -ItemType Directory -Path $operations -Force | Out-Null
$stateLines = @(
    "previous_release=$current"
    "target_release=$TargetRelease"
    "previous_schema=$currentSchema"
    "target_schema=$targetSchema"
    "previous_bundle=$currentVerifiedBundle"
    "target_bundle=$bundle"
    "preupdate_backup=$backup"
    "updated_at=$([DateTime]::UtcNow.ToString('o'))"
)
[IO.File]::WriteAllLines(
    (Join-Path $operations "previous-release.env"),
    [string[]] $stateLines,
    $utf8
)

$lines = if (Test-Path $envFile) { @(Get-Content $envFile) } else { @() }
$found = $false
$lines = $lines | ForEach-Object {
    if ($_ -match "^COOP_RELEASE=") { $found = $true; "COOP_RELEASE=$TargetRelease" } else { $_ }
}
if (-not $found) { $lines += "COOP_RELEASE=$TargetRelease" }
[IO.File]::WriteAllLines($envFile, [string[]] $lines, $utf8)
$env:COOP_RELEASE = $TargetRelease
if ($failpoint -eq "after-release-switch") {
    & (Join-Path $PSScriptRoot "rollback-node.ps1") -Release $current
    throw "Injected update failure after release switch"
}

if ($Build) {
    & docker @compose build migrate api worker frontend gateway
    if ($LASTEXITCODE -ne 0) { throw "Release build failed" }
}
else {
    foreach ($image in @("backend", "frontend", "gateway")) {
        $imageName = "cooperative-clearing/{0}:{1}" -f $image, $TargetRelease
        & docker image inspect $imageName | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Release image is unavailable: $imageName" }
    }
}

& docker @compose stop api worker frontend gateway
if ($LASTEXITCODE -ne 0) {
    & (Join-Path $PSScriptRoot "rollback-node.ps1") -Release $current
    throw "Runtime writer stop failed"
}

try {
    & docker @compose run --rm migrate
    if ($LASTEXITCODE -ne 0) { throw "Migration failed" }
    $migratedSchema = Get-DatabaseSchema
    if ($migratedSchema -ne $targetSchema) {
        throw "Migration reached schema $migratedSchema, expected $targetSchema"
    }
    if ($failpoint -eq "after-migration") {
        throw "Injected update failure after migration"
    }
    & docker @compose up -d api worker frontend gateway
    if ($LASTEXITCODE -ne 0) { throw "Runtime startup failed" }
    if ($failpoint -eq "after-startup") {
        throw "Injected update failure after startup"
    }
    & (Join-Path $PSScriptRoot "verify-stack.ps1") -BaseUrl "http://127.0.0.1:$httpPort"
    & docker @compose run --rm --no-deps api coopctl verify-journal
    if ($LASTEXITCODE -ne 0) { throw "Journal verification failed" }
    & docker @compose run --rm --no-deps api coopctl verify-restore-consistency
    if ($LASTEXITCODE -ne 0) { throw "Restored-state consistency verification failed" }
    if ($environment -eq "production") {
        $contextUpdate = @(
            (Join-Path $PSScriptRoot "runtime_environment.py")
            "configure"
            "--root"
            $root
            "--mode"
            "production"
            "--release"
            $TargetRelease
            "--verified-release-bundle"
            $bundle
            "--release-public-key"
            $releasePublicKey
            "--license-policy-sha256"
            $policySha256
        )
        & python @contextUpdate | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Release recovery context update failed" }
    }
}
catch {
    & (Join-Path $PSScriptRoot "rollback-node.ps1") -Release $current
    throw
}

Write-Host "Updated $current -> $TargetRelease; backup: $backup"
