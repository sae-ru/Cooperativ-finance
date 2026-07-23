[CmdletBinding()]
param(
    [string] $OutputRoot = "evidence",
    [switch] $AllowDirty
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$environment = if ($env:COOP_ENVIRONMENT) { $env:COOP_ENVIRONMENT } else { "dev" }
$timestamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$destination = Join-Path $root (Join-Path $OutputRoot "release-$timestamp")
$gitStatus = (& git -C $root status --porcelain=v1) -join [Environment]::NewLine
if ($environment -eq "prod" -and $gitStatus -and -not $AllowDirty) {
    throw "Production evidence requires a clean Git worktree"
}

New-Item -ItemType Directory -Path $destination -Force | Out-Null

@{
    format = "cooperative-clearing-production-evidence-v1"
    generated_at = [DateTime]::UtcNow.ToString("o")
    environment = $environment
    contains_logs = $false
    contains_raw_pii = $false
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destination "manifest.json") -Encoding utf8

(& git -C $root rev-parse HEAD) | Set-Content -LiteralPath (Join-Path $destination "git-revision.txt") -Encoding utf8
$gitStatus | Set-Content -LiteralPath (Join-Path $destination "git-status.txt") -Encoding utf8
(& docker compose --project-directory $root -f (Join-Path $root "compose.yaml") ps) |
    Set-Content -LiteralPath (Join-Path $destination "stack.txt") -Encoding utf8
(& docker compose --project-directory $root -f (Join-Path $root "compose.yaml") images) |
    Set-Content -LiteralPath (Join-Path $destination "images.txt") -Encoding utf8
(& docker compose --project-directory $root -f (Join-Path $root "compose.yaml") exec -T api coopctl diagnostics) |
    Set-Content -LiteralPath (Join-Path $destination "diagnostics.json") -Encoding utf8
(& docker compose --project-directory $root -f (Join-Path $root "compose.yaml") exec -T api coopctl verify-journal) |
    Set-Content -LiteralPath (Join-Path $destination "journal-verification.json") -Encoding utf8

foreach ($endpoint in @("health/live", "health/ready", "api/v1/system/status")) {
    $name = $endpoint.Replace("/", "-") + ".json"
    & curl.exe --fail --silent --show-error "http://127.0.0.1:8080/$endpoint" |
        Set-Content -LiteralPath (Join-Path $destination $name) -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "Evidence endpoint failed: $endpoint" }
}

foreach ($openApi in @("backend/openapi.json", "frontend/openapi.json")) {
    $path = Join-Path $root $openApi
    if (Test-Path -LiteralPath $path) {
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $openApi" | Add-Content -LiteralPath (Join-Path $destination "openapi-sha256.txt") -Encoding utf8
    }
}

$openApiReport = Join-Path $destination "openapi-compatibility.json"
$openApiArguments = @(
    (Join-Path $root "scripts/openapi_compat.py"),
    "--baseline", (Join-Path $root "infra/contracts/openapi-0.1.0.json"),
    "--current", (Join-Path $root "backend/openapi.json"),
    "--mirror", (Join-Path $root "frontend/openapi.json"),
    "--report", $openApiReport
)
& python @openApiArguments | Out-Null
if ($LASTEXITCODE -ne 0) { throw "OpenAPI compatibility gate failed" }

"complete_at=$([DateTime]::UtcNow.ToString('o'))" |
    Set-Content -LiteralPath (Join-Path $destination "COMPLETE") -Encoding utf8
$checksumLines = Get-ChildItem -LiteralPath $destination -File |
    Where-Object Name -ne "SHA256SUMS" |
    Sort-Object Name |
    ForEach-Object {
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $($_.Name)"
    }
$checksumLines | Set-Content -LiteralPath (Join-Path $destination "SHA256SUMS") -Encoding ascii

Write-Output $destination
