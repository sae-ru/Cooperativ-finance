[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$secrets = Join-Path $root "secrets"
$utf8 = [Text.UTF8Encoding]::new($false)

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

New-HexSecret (Join-Path $secrets "postgres_migrator_password") 32
New-HexSecret (Join-Path $secrets "postgres_app_password") 32
New-HexSecret (Join-Path $secrets "node_signing_seed") 32
New-HexSecret (Join-Path $secrets "blob_encryption_key") 32
New-HexSecret (Join-Path $secrets "bootstrap_registrar_password") 32
New-HexSecret (Join-Path $secrets "bootstrap_security_password") 32
New-HexSecret (Join-Path $secrets "bootstrap_auditor_password") 32

$environmentFile = Join-Path $root ".env"
if (-not (Test-Path -LiteralPath $environmentFile)) {
    Copy-Item -LiteralPath (Join-Path $root ".env.example") -Destination $environmentFile
}

Write-Host "Node secrets and non-secret configuration are ready."
