[CmdletBinding()]
param(
    [string] $BaseUrl = "http://host.docker.internal:8080",
    [string] $HostHeader = "127.0.0.1",
    [ValidateSet("/health/live", "/api/v1/system/status")]
    [string] $Endpoint = "/health/live",
    [ValidateRange(1, 1000000)]
    [int] $Requests = 500,
    [ValidateRange(1, 10000)]
    [int] $Concurrency = 20,
    [ValidateRange(0.0, 1.0)]
    [double] $MaxErrorRate = 0,
    [ValidateRange(1.0, 600000.0)]
    [double] $MaxP95Ms = 250,
    [ValidateRange(0.0, 1000000.0)]
    [double] $MinRps = 10
)

$ErrorActionPreference = "Stop"
$release = if ($env:COOP_RELEASE) { $env:COOP_RELEASE } else { "0.1.0-dev" }
& docker run --rm "cooperative-clearing/backend:$release" `
    python -m cooperative_clearing.tools.capacity `
    --base-url $BaseUrl `
    --endpoint $Endpoint `
    --host-header $HostHeader `
    --requests $Requests `
    --concurrency $Concurrency `
    --max-error-rate $MaxErrorRate.ToString([Globalization.CultureInfo]::InvariantCulture) `
    --max-p95-ms $MaxP95Ms.ToString([Globalization.CultureInfo]::InvariantCulture) `
    --min-rps $MinRps.ToString([Globalization.CultureInfo]::InvariantCulture)
if ($LASTEXITCODE -ne 0) { throw "Capacity smoke failed" }
