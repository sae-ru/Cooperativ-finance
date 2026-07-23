[CmdletBinding()]
param([string] $Release)

$ErrorActionPreference = "Stop"
$utf8 = [Text.UTF8Encoding]::new($false)
$root = Split-Path -Parent $PSScriptRoot
$state = Join-Path $root ".operations/previous-release.env"
$compose = @("compose", "--project-directory", $root, "-f", (Join-Path $root "compose.yaml"))
if (-not $Release) {
    if (-not (Test-Path $state)) { throw "No previous release state is available" }
    $line = Get-Content $state | Where-Object { $_ -match "^previous_release=" }
    $Release = $line.Substring("previous_release=".Length)
}
if ($Release -notmatch "^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$") {
    throw "Invalid rollback release identifier"
}
foreach ($image in @("backend", "frontend", "gateway")) {
    $imageName = "cooperative-clearing/{0}:{1}" -f $image, $Release
    & docker image inspect $imageName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Rollback image is unavailable: $imageName" }
}

$envFile = Join-Path $root ".env"
$lines = if (Test-Path $envFile) { @(Get-Content $envFile) } else { @() }
$found = $false
$lines = $lines | ForEach-Object {
    if ($_ -match "^COOP_RELEASE=") { $found = $true; "COOP_RELEASE=$Release" } else { $_ }
}
if (-not $found) { $lines += "COOP_RELEASE=$Release" }
[IO.File]::WriteAllLines($envFile, [string[]] $lines, $utf8)
$env:COOP_RELEASE = $Release

# Deliberately no Alembic downgrade. Use restore-node.ps1 if the old app is not
# compatible with the expanded schema.
& docker @compose up -d api worker frontend gateway
if ($LASTEXITCODE -ne 0) { throw "Application rollback startup failed" }
$httpPort = if ($env:COOP_HTTP_PORT) { $env:COOP_HTTP_PORT } else { $null }
if (-not $httpPort) {
    $portLine = $lines |
        Where-Object { $_ -match "^COOP_HTTP_PORT=" } |
        Select-Object -Last 1
    if ($portLine) { $httpPort = $portLine.Substring("COOP_HTTP_PORT=".Length) }
}
if (-not $httpPort) { $httpPort = "8080" }
try {
    & (Join-Path $PSScriptRoot "verify-stack.ps1") -BaseUrl "http://127.0.0.1:$httpPort"
    & docker @compose run --rm --no-deps api coopctl verify-journal
    if ($LASTEXITCODE -ne 0) { throw "Journal verification failed" }
}
catch {
    if (Test-Path $state) {
        $backup = Get-Content $state | Where-Object { $_ -match "^preupdate_backup=" }
        Write-Error "Application rollback failed. Restore the pre-update backup: $backup"
    }
    throw
}

New-Item -ItemType Directory -Path (Join-Path $root ".operations") -Force | Out-Null
$rollbackLines = @(
    "rolled_back_to=$Release"
    "rolled_back_at=$([DateTime]::UtcNow.ToString('o'))"
)
[IO.File]::WriteAllLines(
    (Join-Path $root ".operations/last-rollback.env"),
    [string[]] $rollbackLines,
    $utf8
)
Write-Host "Application rollback completed: $Release"
