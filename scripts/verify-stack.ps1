[CmdletBinding()]
param(
    [string] $BaseUrl = "http://127.0.0.1:8080",
    [switch] $VerifyBootstrapLogin,
    [ValidateRange(1, 120)]
    [int] $Attempts = 30,
    [ValidateRange(0, 30)]
    [int] $DelaySeconds = 2
)

$ErrorActionPreference = "Stop"

$verified = $false
foreach ($attempt in 1..$Attempts) {
    try {
        $live = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/health/live"
        $ready = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/health/ready"
        $status = Invoke-RestMethod -Uri "$BaseUrl/api/v1/system/status"
        if ($live.StatusCode -ne 200 -or $ready.StatusCode -ne 200) {
            throw "Health endpoint returned a non-success status"
        }
        if ($status.data.status -ne "OPERATIONAL") {
            throw "Node is not operational: $($status.data.status)"
        }
        if ($status.data.worker.status -ne "RUNNING") {
            throw "Worker is not running: $($status.data.worker.status)"
        }
        $verified = $true
        break
    }
    catch {
        if ($attempt -eq $Attempts) { throw }
        Start-Sleep -Seconds $DelaySeconds
    }
}
if (-not $verified) { throw "Stack did not become ready" }

if ($VerifyBootstrapLogin) {
    $root = Split-Path -Parent $PSScriptRoot
    $password = [IO.File]::ReadAllText((Join-Path $root "secrets/bootstrap_registrar_password")).Trim()
    $login = Invoke-RestMethod -Uri "$BaseUrl/api/v1/auth/login" -Method Post -ContentType "application/json" -Body (@{ login = "registrar"; password = $password } | ConvertTo-Json)
    $headers = @{ Authorization = "Bearer $($login.data.access_token)" }
    $principal = Invoke-RestMethod -Uri "$BaseUrl/api/v1/auth/me" -Headers $headers
    $roles = @($principal.data.roles | ForEach-Object { $_.role } | Sort-Object -Unique)
    $requiredRoles = @("COOPERATIVE_ADMIN", "MEMBER_REGISTRAR")
    $missingRoles = @($requiredRoles | Where-Object { $_ -notin $roles })
    if (-not $principal.data.must_change_password -or $missingRoles.Count -gt 0) {
        throw "Bootstrap registrar identity policy is not satisfied"
    }
}

Write-Host "Stack verification passed: $BaseUrl"