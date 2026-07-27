[CmdletBinding()]
param(
    [string] $BackupRoot,
    [string] $EncryptedRecoveryBundle,
    [string] $VerifiedReleaseBundle
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object Text.UTF8Encoding($false)
$root = Split-Path -Parent $PSScriptRoot
function Get-RuntimeSetting([string] $Name) {
    $output = @(& python (Join-Path $PSScriptRoot "runtime_environment.py") get --root $root --name $Name)
    if ($LASTEXITCODE -ne 0) { throw "Runtime setting resolution failed: $Name" }
    if ($output.Count -eq 0) { return "" }
    return $output[-1].Trim()
}
$releasePublicKey = if ($env:COOP_RELEASE_PUBLIC_KEY) {
    $env:COOP_RELEASE_PUBLIC_KEY
} else { Get-RuntimeSetting "COOP_RELEASE_PUBLIC_KEY" }
$policySha256 = if ($env:COOP_RELEASE_LICENSE_POLICY_SHA256) {
    $env:COOP_RELEASE_LICENSE_POLICY_SHA256
} else { Get-RuntimeSetting "COOP_RELEASE_LICENSE_POLICY_SHA256" }
if (-not $BackupRoot) { $BackupRoot = Join-Path $root "backups" }
$BackupRoot = [IO.Path]::GetFullPath($BackupRoot)
$project = if ($env:COMPOSE_PROJECT_NAME) { $env:COMPOSE_PROJECT_NAME } else { "cooperative-clearing" }
$compose = @("compose", "--project-directory", $root, "-f", (Join-Path $root "compose.yaml"))
$release = if ($env:COOP_RELEASE) { $env:COOP_RELEASE } else { $null }
if (-not $release -and (Test-Path (Join-Path $root ".env"))) {
    $releaseLine = Get-Content (Join-Path $root ".env") |
        Where-Object { $_ -match "^COOP_RELEASE=" } |
        Select-Object -Last 1
    if ($releaseLine) { $release = $releaseLine.Substring("COOP_RELEASE=".Length) }
}
if (-not $release) { $release = "0.1.0-dev" }
if (-not $VerifiedReleaseBundle) {
    $VerifiedReleaseBundle = if ($env:COOP_VERIFIED_RELEASE_BUNDLE) {
        $env:COOP_VERIFIED_RELEASE_BUNDLE
    } else { Get-RuntimeSetting "COOP_VERIFIED_RELEASE_BUNDLE" }
}
$releaseMaterial = "external-required"
$releaseManifestHash = "none"
$timestamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$backupId = "node-$timestamp"
$work = Join-Path $BackupRoot ".$backupId.$([Guid]::NewGuid().ToString('N'))"
$final = Join-Path $BackupRoot $backupId
$stopped = $false

New-Item -ItemType Directory -Path $work -Force | Out-Null
try {
    & docker @compose up -d postgres | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL startup failed" }
    & docker @compose exec -T postgres pg_isready -U coop_migrator -d cooperative_clearing | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL is not ready" }

    $apiContainer = (& docker @compose ps -q api).Trim()
    $workerContainer = (& docker @compose ps -q worker).Trim()
    if (-not $apiContainer -or -not $workerContainer) {
        throw "API and worker containers must exist"
    }
    if ((& docker inspect --format "{{.State.Running}}" $apiContainer) -ne "true" -or
        (& docker inspect --format "{{.State.Running}}" $workerContainer) -ne "true") {
        throw "API and worker must be running before a coordinated backup"
    }
    $journal = @(& docker @compose exec -T api coopctl verify-journal)
    if ($LASTEXITCODE -ne 0) { throw "Journal verification failed" }
    [IO.File]::WriteAllLines((Join-Path $work "journal-verification.json"), [string[]] $journal, $utf8)

    if ($VerifiedReleaseBundle) {
        if (-not $releasePublicKey) {
            throw "COOP_RELEASE_PUBLIC_KEY is required to include a release in backup"
        }
        $bundle = (Resolve-Path -LiteralPath $VerifiedReleaseBundle).Path
        $verification = @(
            (Join-Path $PSScriptRoot "release_bundle.py")
            "verify"
            "--bundle"
            $bundle
            "--public-key"
            $releasePublicKey
            "--expected-release"
            $release
        )
        if ($policySha256) {
            $verification += @(
                "--expected-policy-sha256",
                $policySha256
            )
        }
        & python @verification | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Release bundle verification failed" }
        $releaseTarget = Join-Path $work "release"
        New-Item -ItemType Directory -Path $releaseTarget | Out-Null
        Get-ChildItem -LiteralPath $bundle -Force |
            Copy-Item -Destination $releaseTarget -Recurse -Force
        $releaseMaterial = "included-verified"
        $releaseManifestHash = (
            Get-FileHash -LiteralPath (Join-Path $releaseTarget "release-manifest.json") -Algorithm SHA256
        ).Hash.ToLowerInvariant()
    }

    & docker stop $apiContainer $workerContainer | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not quiesce application writers" }
    $stopped = $true

    $dump = Join-Path $work "database.dump"
    $dumpArguments = @($compose) + @(
        "exec", "-T", "postgres", "pg_dump",
        "-U", "coop_migrator", "-d", "cooperative_clearing",
        "--format=custom", "--compress=9", "--no-owner"
    )
    $dumpProcess = Start-Process -FilePath "docker" -ArgumentList $dumpArguments -NoNewWindow -Wait -PassThru -RedirectStandardOutput $dump
    if ($dumpProcess.ExitCode -ne 0) { throw "Database backup failed" }

    & docker run --rm -v "$($project)_blob-data:/source:ro" -v "$($work):/backup" postgres:18-alpine sh -ec "tar -C /source -czf /backup/blobs.tar.gz ."
    if ($LASTEXITCODE -ne 0) { throw "Blob backup failed" }

    $schema = @(& docker @compose exec -T postgres psql -U coop_migrator -d cooperative_clearing -Atc "select version_num from alembic_version")
    if ($LASTEXITCODE -ne 0) { throw "Schema inspection failed" }
    [IO.File]::WriteAllLines((Join-Path $work "schema.txt"), [string[]] $schema, $utf8)

    Copy-Item (Join-Path $root "compose.yaml") (Join-Path $work "compose.yaml")
    if (Test-Path (Join-Path $root ".env")) {
        $safeEnvironment = @(
            Get-Content (Join-Path $root ".env") |
                Where-Object { $_ -notmatch "(?i)PASSWORD|SECRET|TOKEN|PRIVATE|SIGNING|ENCRYPTION" }
        )
        [IO.File]::WriteAllLines((Join-Path $work "runtime.env"), [string[]] $safeEnvironment, $utf8)
    }

    $recoveryMaterial = "external-required"
    if ($EncryptedRecoveryBundle) {
        $recoveryBundle = (Resolve-Path -LiteralPath $EncryptedRecoveryBundle).Path
        Copy-Item -LiteralPath $recoveryBundle -Destination (Join-Path $work "recovery.bundle.enc")
        $recoveryMaterial = "included-encrypted"
    }
    $kind = if (
        $recoveryMaterial -eq "included-encrypted" -and
        $releaseMaterial -eq "included-verified"
    ) { "FULL" } else { "DATA_ONLY" }
    $manifestLines = @(
        "format=cooperative-clearing-backup-v1"
        "backup_id=$backupId"
        "backup_kind=$kind"
        "created_at=$timestamp"
        "release=$release"
        "schema=$(($schema -join ' ').Trim())"
        "database=database.dump"
        "blobs=blobs.tar.gz"
        "recovery_material=$recoveryMaterial"
        "release_material=$releaseMaterial"
        "release_manifest_sha256=$releaseManifestHash"
    )
    [IO.File]::WriteAllLines((Join-Path $work "manifest.env"), [string[]] $manifestLines, $utf8)

    $files = @("database.dump", "blobs.tar.gz", "journal-verification.json", "schema.txt", "compose.yaml", "manifest.env")
    if (Test-Path (Join-Path $work "runtime.env")) { $files += "runtime.env" }
    if ($recoveryMaterial -eq "included-encrypted") {
        $files += "recovery.bundle.enc"
    }
    if ($releaseMaterial -eq "included-verified") {
        $workPrefix = $work.TrimEnd("\", "/") + [IO.Path]::DirectorySeparatorChar
        $files += @(
            Get-ChildItem -LiteralPath (Join-Path $work "release") -File -Recurse |
                ForEach-Object {
                    $_.FullName.Substring($workPrefix.Length).Replace("\", "/")
                } |
                Sort-Object
        )
    }
    $lines = foreach ($name in $files) {
        $hash = (Get-FileHash -LiteralPath (Join-Path $work $name) -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $name"
    }
    [IO.File]::WriteAllLines((Join-Path $work "SHA256SUMS"), [string[]] $lines, [Text.Encoding]::ASCII)

    & docker run --rm -v "$($work):/backup:ro" postgres:18-alpine sh -ec "pg_restore --list /backup/database.dump >/dev/null; tar -tzf /backup/blobs.tar.gz >/dev/null"
    if ($LASTEXITCODE -ne 0) { throw "Backup archive validation failed" }
    [IO.File]::WriteAllText((Join-Path $work "COMPLETE"), $timestamp + [Environment]::NewLine, [Text.Encoding]::ASCII)
    Move-Item -LiteralPath $work -Destination $final
    $work = $null
}
finally {
    if ($stopped) { & docker start $apiContainer $workerContainer | Out-Null }
    if ($work -and (Test-Path -LiteralPath $work)) {
        Remove-Item -LiteralPath $work -Recurse -Force
    }
}

Write-Output $final