[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $BundleDirectory,
    [Parameter(Mandatory)]
    [string] $PublicKey,
    [string] $ExpectedRelease,
    [ValidateSet("linux/amd64", "linux/arm64")]
    [string] $ExpectedPlatform
)

$ErrorActionPreference = "Stop"
$arguments = @(
    (Join-Path $PSScriptRoot "release_bundle.py")
    "verify"
    "--bundle"
    $BundleDirectory
    "--public-key"
    $PublicKey
)
if ($ExpectedRelease) { $arguments += @("--expected-release", $ExpectedRelease) }
if ($ExpectedPlatform) { $arguments += @("--expected-platform", $ExpectedPlatform) }
if ($env:COOP_RELEASE_LICENSE_POLICY_SHA256) {
    $arguments += @(
        "--expected-policy-sha256",
        $env:COOP_RELEASE_LICENSE_POLICY_SHA256
    )
}
& python @arguments
if ($LASTEXITCODE -ne 0) { throw "Release bundle verification failed" }