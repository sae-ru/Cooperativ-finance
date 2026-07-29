[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern("^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")]
    [string] $Release,
    [Parameter(Mandatory)]
    [string] $OutputDirectory,
    [Parameter(Mandatory)]
    [string] $PrivateKey,
    [Parameter(Mandatory)]
    [ValidateSet("linux/amd64", "linux/arm64")]
    [string] $QualifiedPlatform,
    [string] $FrontendAuditImage = "cooperative-clearing/frontend-test:local",
    [string[]] $UpgradeFrom = @()
)

$ErrorActionPreference = "Stop"
$arguments = @(
    (Join-Path $PSScriptRoot "release_bundle.py")
    "create"
    "--release"
    $Release
    "--output"
    $OutputDirectory
    "--private-key"
    $PrivateKey
    "--qualified-platform"
    $QualifiedPlatform
    "--frontend-audit-image"
    $FrontendAuditImage
)
foreach ($source in $UpgradeFrom) {
    $arguments += @("--upgrade-from", $source)
}
& python @arguments
if ($LASTEXITCODE -ne 0) { throw "Release bundle creation failed" }