[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int] $Port = 18088,
    [string] $Project = "",
    [switch] $SkipBuild
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$timestamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
if (-not $Project) {
    $Project = "coop-observability-$($timestamp.ToLowerInvariant())"
}
$destinationRoot = if ($env:COOP_OBSERVABILITY_EVIDENCE_ROOT) {
    $env:COOP_OBSERVABILITY_EVIDENCE_ROOT
} else {
    Join-Path $root "evidence"
}
$destination = Join-Path $destinationRoot "local-observability-$timestamp"
$passwordFile = Join-Path $root "secrets\bootstrap_security_password"
$expectedSchema = "0039_participant_address_events"
$composeArgs = @(
    "compose",
    "--project-name", $Project,
    "--project-directory", $root,
    "-f", (Join-Path $root "compose.yaml"),
    "-f", (Join-Path $root "compose.observability-test.yaml"),
    "--profile", "observability"
)
$previousPort = $env:COOP_HTTP_PORT
$previousEvidenceDir = $env:COOP_OBSERVABILITY_EVIDENCE_DIR
$env:COOP_HTTP_PORT = [string]$Port
$env:COOP_OBSERVABILITY_EVIDENCE_DIR = $destination

function Invoke-Compose {
    param([Parameter(Mandatory)][string[]] $CommandArgs)
    & docker @composeArgs @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose command failed: $($CommandArgs -join ' ')"
    }
}

function Invoke-OperatorProbe {
    param([Parameter(Mandatory)][string] $OutputPath)
    $probeArgs = @(
        "run", "--rm", "--no-deps", "observability-probe",
        "python", "/workspace/scripts/local_observability_probe.py",
        "--base-url", "http://gateway:8080",
        "--allow-internal-host", "gateway",
        "--login", "security",
        "--password-file", "/run/secrets/operator_password",
        "--expected-schema", $expectedSchema,
        "--network-evidence", "/evidence/network-isolation.json",
        "--logs", "/evidence/runtime.log",
        "--report", "-"
    )
    $stderrPath = "$OutputPath.stderr"
    try {
        $commandPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $output = & docker @composeArgs @probeArgs 2> $stderrPath
            $probeExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $commandPreference
        }
        if ($probeExitCode -ne 0) {
            $diagnostic = Get-Content -Path $stderrPath -ErrorAction SilentlyContinue |
                Where-Object { $_.Trim() } |
                Select-Object -Last 1
            if (-not $diagnostic) { $diagnostic = "no diagnostic output" }
            throw "Local observability operator probe failed: $diagnostic"
        }
    }
    finally {
        Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    }
    [IO.File]::WriteAllText(
        $OutputPath,
        ($output -join [Environment]::NewLine) + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
}

try {
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    $migratorPassword = Join-Path $root "secrets\postgres_migrator_password"
    if (-not (Test-Path -LiteralPath $migratorPassword) -or
        -not (Test-Path -LiteralPath $passwordFile)) {
        & (Join-Path $PSScriptRoot "bootstrap-node.ps1")
        if ($LASTEXITCODE -ne 0) { throw "Unable to create local test secrets" }
    }

    & python (Join-Path $PSScriptRoot "operational_status.py") probe --root $root | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Unable to write the local host probe" }

    Invoke-Compose @("down", "--volumes", "--remove-orphans")
    if (-not $SkipBuild -and $env:COOP_OBSERVABILITY_SKIP_BUILD -ne "1") {
        Invoke-Compose @("build", "api", "frontend", "gateway")
    }
    Invoke-Compose @("up", "--detach", "--wait", "gateway", "worker")

    $networkState = [ordered]@{}
    foreach ($network in @("edge", "app", "web", "data")) {
        $networkName = $Project + "_" + $network
        $value = & docker network inspect $networkName --format "{{.Internal}}"
        if ($LASTEXITCODE -ne 0 -or $value.Trim() -ne "true") {
            throw "Network $networkName is not internal"
        }
        $networkState[$network] = $true
    }

    $probePreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & docker @composeArgs exec -T gateway wget -q -T 3 -O /dev/null http://198.51.100.1 2>$null
    $egressExitCode = $LASTEXITCODE
    $ErrorActionPreference = $probePreference
    if ($egressExitCode -eq 0) {
        throw "Gateway unexpectedly reached a non-local address"
    }

    $networkEvidence = [ordered]@{
        format = "cooperative-clearing-network-isolation-v1"
        networks = $networkState
        egress_probe = "BLOCKED"
    } | ConvertTo-Json -Depth 4
    $networkPath = Join-Path $destination "network-isolation.json"
    [IO.File]::WriteAllText(
        $networkPath,
        $networkEvidence + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )

    $logPath = Join-Path $destination "runtime.log"

    $logs = & docker @composeArgs logs --no-color --tail 500 api worker gateway 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Unable to collect local runtime logs" }
    [IO.File]::WriteAllText(
        $logPath,
        ($logs -join [Environment]::NewLine) + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )

    Invoke-OperatorProbe (Join-Path $destination "report.json")

    $checksumLines = foreach ($file in Get-ChildItem -LiteralPath $destination -File |
        Where-Object Name -ne "SHA256SUMS" |
        Sort-Object Name) {
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $($file.Name)"
    }
    [IO.File]::WriteAllText(
        (Join-Path $destination "SHA256SUMS"),
        ($checksumLines -join "`n") + "`n",
        [Text.UTF8Encoding]::new($false)
    )
    Write-Output $destination
}
finally {
    if ($env:KEEP_LOCAL_OBSERVABILITY_STACK -ne "1") {
        $cleanupPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & docker @composeArgs down --volumes --remove-orphans *> $null
        $ErrorActionPreference = $cleanupPreference
    }
    if ($null -eq $previousPort) {
        Remove-Item Env:COOP_HTTP_PORT -ErrorAction SilentlyContinue
    } else {
        $env:COOP_HTTP_PORT = $previousPort
    }
    if ($null -eq $previousEvidenceDir) {
        Remove-Item Env:COOP_OBSERVABILITY_EVIDENCE_DIR -ErrorAction SilentlyContinue
    } else {
        $env:COOP_OBSERVABILITY_EVIDENCE_DIR = $previousEvidenceDir
    }
}