[CmdletBinding()]
param(
    [ValidateSet("demo", "production")]
    [string] $Mode,
    [switch] $DemoCredentials,
    [string] $Release
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$secrets = Join-Path $root "secrets"
$utf8 = [Text.UTF8Encoding]::new($false)

if ($DemoCredentials -and $Mode -and $Mode -ne "demo") {
    throw "Demo credentials are only valid in demo mode"
}
if ($Mode) {
    $configuration = @(
        (Join-Path $PSScriptRoot "runtime_environment.py")
        "configure"
        "--root"
        $root
        "--mode"
        $Mode
    )
    if ($Release) {
        $configuration += @("--release", $Release)
    }
    & python @configuration
    if ($LASTEXITCODE -ne 0) { throw "Runtime environment configuration failed" }
}

[IO.Directory]::CreateDirectory($secrets) | Out-Null

function New-HexSecret([string] $Path, [int] $Bytes) {
    if (Test-Path -LiteralPath $Path) {
        if ((Get-Item -LiteralPath $Path).Length -gt 0) {
            return
        }
    }
    $buffer = [byte[]]::new($Bytes)
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($buffer)
    }
    finally {
        $generator.Dispose()
    }
    $value = -join ($buffer | ForEach-Object { $_.ToString("x2") })
    [IO.File]::WriteAllText($Path, $value + "`n", $utf8)
}

function New-InitialPassword([string] $Path, [string] $DemoValue) {
    if (Test-Path -LiteralPath $Path) {
        if ((Get-Item -LiteralPath $Path).Length -gt 0) {
            return
        }
    }
    if ($DemoCredentials -or $Mode -eq "demo") {
        [IO.File]::WriteAllText($Path, $DemoValue + "`n", $utf8)
    }
    else {
        New-HexSecret $Path 32
    }
}

New-HexSecret (Join-Path $secrets "postgres_migrator_password") 32
New-HexSecret (Join-Path $secrets "postgres_app_password") 32
New-HexSecret (Join-Path $secrets "node_signing_seed") 32
New-HexSecret (Join-Path $secrets "blob_encryption_key") 32
New-HexSecret (Join-Path $secrets "mfa_encryption_key") 32
New-InitialPassword (Join-Path $secrets "bootstrap_registrar_password") "CoopDemo-Registrar-2026!"
New-InitialPassword (Join-Path $secrets "bootstrap_security_password") "CoopDemo-Security-2026!"
New-InitialPassword (Join-Path $secrets "bootstrap_auditor_password") "CoopDemo-Auditor-2026!"

$environmentFile = Join-Path $root ".env"
if (-not (Test-Path -LiteralPath $environmentFile)) {
    Copy-Item -LiteralPath (Join-Path $root ".env.example") -Destination $environmentFile
}

Write-Host "Node secrets and non-secret configuration are ready."
