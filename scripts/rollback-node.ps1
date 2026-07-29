[CmdletBinding()]
param([string] $Release)

$ErrorActionPreference = "Stop"
$utf8 = [Text.UTF8Encoding]::new($false)
$root = Split-Path -Parent $PSScriptRoot
$state = Join-Path $root ".operations/previous-release.env"
$compose = @("compose", "--project-directory", $root, "-f", (Join-Path $root "compose.yaml"))
if (-not (Test-Path $state)) { throw "No verified previous release state is available" }

function Get-StateValue([string] $Name) {
    $line = Get-Content $state | Where-Object { $_ -match "^$([Regex]::Escape($Name))=" } | Select-Object -Last 1
    if (-not $line) { return "" }
    return $line.Substring($Name.Length + 1)
}
function Get-RuntimeSetting([string] $Name) {
    $output = @(& python (Join-Path $PSScriptRoot "runtime_environment.py") get --root $root --name $Name)
    if ($LASTEXITCODE -ne 0) { throw "Runtime setting resolution failed: $Name" }
    if ($output.Count -eq 0) { return "" }
    return $output[-1].Trim()
}
function Get-DatabaseSchema {
    $output = @(& docker @compose run --rm --no-deps api alembic current)
    if ($LASTEXITCODE -ne 0) { throw "Database schema resolution failed" }
    $matches = @($output | Where-Object { $_ -match "^([0-9A-Za-z][0-9A-Za-z_-]{0,127})(?: \(head\))?$" })
    if ($matches.Count -ne 1) { throw "Cannot determine one current database schema revision" }
    [void] ($matches[0] -match "^([0-9A-Za-z][0-9A-Za-z_-]{0,127})(?: \(head\))?$")
    return $Matches[1]
}
function Get-JournalReport {
    $output = @(& docker @compose run --rm --no-deps api coopctl verify-journal)
    if ($LASTEXITCODE -ne 0) { throw "Journal verification failed" }
    $json = $output | Where-Object { $_ -match '^\{' } | Select-Object -Last 1
    if (-not $json) { throw "Journal verifier returned no JSON report" }
    return ($json | ConvertFrom-Json)
}

$previousRelease = Get-StateValue "previous_release"
$targetRelease = Get-StateValue "target_release"
$previousSchema = Get-StateValue "previous_schema"
$targetSchema = Get-StateValue "target_schema"
$previousBundle = Get-StateValue "previous_bundle"
$targetBundle = Get-StateValue "target_bundle"
$backup = Get-StateValue "preupdate_backup"
if (-not $Release) { $Release = $previousRelease }
foreach ($value in @($Release, $previousRelease, $targetRelease)) {
    if ($value -notmatch "^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$") {
        throw "Invalid rollback release state"
    }
}
if ($Release -ne $previousRelease) {
    throw "Rollback release does not match the verified previous release state"
}
foreach ($value in @($previousSchema, $targetSchema)) {
    if ($value -notmatch "^[0-9A-Za-z][0-9A-Za-z_-]{0,127}$") {
        throw "Invalid rollback schema state"
    }
}

$environmentOutput = @(& python (Join-Path $PSScriptRoot "runtime_environment.py") resolve --root $root)
if ($LASTEXITCODE -ne 0) { throw "Runtime environment resolution failed" }
$environment = $environmentOutput[-1].Trim()
$releasePublicKey = if ($env:COOP_RELEASE_PUBLIC_KEY) {
    $env:COOP_RELEASE_PUBLIC_KEY
} else { Get-RuntimeSetting "COOP_RELEASE_PUBLIC_KEY" }
$policySha256 = if ($env:COOP_RELEASE_LICENSE_POLICY_SHA256) {
    $env:COOP_RELEASE_LICENSE_POLICY_SHA256
} else { Get-RuntimeSetting "COOP_RELEASE_LICENSE_POLICY_SHA256" }
if ($environment -eq "production" -and (
    -not $releasePublicKey -or -not $policySha256 -or -not $previousBundle -or -not $targetBundle
)) {
    throw "Production rollback requires both signed bundles, the release key and pinned policy"
}

if ($targetBundle -or $previousBundle) {
    if (-not $targetBundle -or -not $previousBundle -or -not $releasePublicKey) {
        throw "Verified rollback requires both release bundles and the release public key"
    }
    $targetBundle = (Resolve-Path -LiteralPath $targetBundle).Path
    $previousBundle = (Resolve-Path -LiteralPath $previousBundle).Path
    $targetVerification = @(
        (Join-Path $PSScriptRoot "release_bundle.py")
        "verify"
        "--bundle"
        $targetBundle
        "--public-key"
        $releasePublicKey
        "--expected-release"
        $targetRelease
        "--installed-release"
        $previousRelease
        "--installed-schema"
        $previousSchema
    )
    $previousVerification = @(
        (Join-Path $PSScriptRoot "release_bundle.py")
        "verify"
        "--bundle"
        $previousBundle
        "--public-key"
        $releasePublicKey
        "--expected-release"
        $previousRelease
        "--load-images"
    )
    if ($policySha256) {
        $targetVerification += @("--expected-policy-sha256", $policySha256)
        $previousVerification += @("--expected-policy-sha256", $policySha256)
    }
    $targetOutput = @(& python @targetVerification)
    if ($LASTEXITCODE -ne 0) { throw "Target release transition verification failed" }
    $previousOutput = @(& python @previousVerification)
    if ($LASTEXITCODE -ne 0) { throw "Previous release bundle verification failed" }
    $targetResult = $targetOutput[-1] | ConvertFrom-Json
    $previousResult = $previousOutput[-1] | ConvertFrom-Json
    if (
        [string] $targetResult.database_schema_revision -ne $targetSchema -or
        [string] $previousResult.database_schema_revision -ne $previousSchema
    ) {
        throw "Rollback state does not match the signed release schema contracts"
    }
}
else {
    foreach ($image in @("backend", "frontend", "gateway")) {
        $imageName = "cooperative-clearing/{0}:{1}" -f $image, $Release
        & docker image inspect $imageName | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Rollback image is unavailable: $imageName" }
    }
}

& docker @compose stop api worker frontend gateway
if ($LASTEXITCODE -ne 0) { throw "Runtime writer stop failed" }
$before = Get-JournalReport
$currentSchema = Get-DatabaseSchema
if ($currentSchema -eq $targetSchema -and $targetSchema -ne $previousSchema) {
    & docker @compose run --rm --no-deps migrate alembic downgrade $previousSchema
    if ($LASTEXITCODE -ne 0) { throw "Schema downgrade failed; restore backup: $backup" }
}
elseif ($currentSchema -ne $previousSchema) {
    throw "Rollback cannot proceed from unexpected schema $currentSchema; restore backup: $backup"
}
if ((Get-DatabaseSchema) -ne $previousSchema) {
    throw "Schema rollback verification failed; restore backup: $backup"
}
& docker @compose run --rm --no-deps api coopctl verify-restore-consistency
if ($LASTEXITCODE -ne 0) { throw "Restored-state consistency verification failed" }

$envFile = Join-Path $root ".env"
$lines = if (Test-Path $envFile) { @(Get-Content $envFile) } else { @() }
$found = $false
$lines = $lines | ForEach-Object {
    if ($_ -match "^COOP_RELEASE=") { $found = $true; "COOP_RELEASE=$Release" } else { $_ }
}
if (-not $found) { $lines += "COOP_RELEASE=$Release" }
[IO.File]::WriteAllLines($envFile, [string[]] $lines, $utf8)
$env:COOP_RELEASE = $Release

if ($environment -eq "production") {
    & docker @compose up -d --no-build --pull never --force-recreate api worker frontend gateway
}
else {
    & docker @compose up -d --force-recreate api worker frontend gateway
}
if ($LASTEXITCODE -ne 0) { throw "Application rollback startup failed" }
$httpPort = if ($env:COOP_HTTP_PORT) { $env:COOP_HTTP_PORT } else { $null }
if (-not $httpPort) {
    $portLine = $lines | Where-Object { $_ -match "^COOP_HTTP_PORT=" } | Select-Object -Last 1
    if ($portLine) { $httpPort = $portLine.Substring("COOP_HTTP_PORT=".Length) }
}
if (-not $httpPort) { $httpPort = "8080" }
& (Join-Path $PSScriptRoot "verify-stack.ps1") -BaseUrl "http://127.0.0.1:$httpPort"
$after = Get-JournalReport
if ($before.last_sequence -ne $after.last_sequence -or $before.last_event_hash -ne $after.last_event_hash) {
    throw "Rollback changed accepted journal history; restore backup: $backup"
}
if ($environment -eq "production") {
    $contextUpdate = @(
        (Join-Path $PSScriptRoot "runtime_environment.py")
        "configure"
        "--root"
        $root
        "--mode"
        "production"
        "--release"
        $previousRelease
        "--verified-release-bundle"
        $previousBundle
        "--release-public-key"
        $releasePublicKey
        "--license-policy-sha256"
        $policySha256
    )
    & python @contextUpdate | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Rollback recovery context update failed" }
}

New-Item -ItemType Directory -Path (Join-Path $root ".operations") -Force | Out-Null
$rollbackLines = @(
    "rolled_back_to=$Release"
    "rolled_back_schema=$previousSchema"
    "journal_last_sequence=$($after.last_sequence)"
    "journal_last_event_hash=$($after.last_event_hash)"
    "rolled_back_at=$([DateTime]::UtcNow.ToString('o'))"
)
[IO.File]::WriteAllLines(
    (Join-Path $root ".operations/last-rollback.env"),
    [string[]] $rollbackLines,
    $utf8
)
Write-Host "Application/schema rollback completed: $Release@$previousSchema; journal sequence: $($after.last_sequence)"