[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern("^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")]
    [string] $Release,
    [Parameter(Mandatory)]
    [string] $OutputDirectory,
    [Parameter(Mandatory)]
    [string] $PrivateKey,
    [string] $FrontendAuditImage = "cooperative-clearing/frontend-test:local"
)

$ErrorActionPreference = "Stop"
& python (Join-Path $PSScriptRoot "release_bundle.py") create `
    --release $Release `
    --output $OutputDirectory `
    --private-key $PrivateKey `
    --frontend-audit-image $FrontendAuditImage
if ($LASTEXITCODE -ne 0) { throw "Release bundle creation failed" }