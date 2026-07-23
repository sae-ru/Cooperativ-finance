[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $BackupDirectory
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backup = (Resolve-Path -LiteralPath $BackupDirectory).Path
$suffix = "{0}-{1}" -f ([DateTime]::UtcNow.ToString("yyyyMMddHHmmss")), $PID
$container = "coop-restore-drill-$suffix"
$network = $container
$dbVolume = "$container-db"
$blobVolume = "$container-blobs"
$password = ([Guid]::NewGuid().ToString("N") + [Guid]::NewGuid().ToString("N"))

function Invoke-Docker {
    & docker @args
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed: docker $($args -join ' ')"
    }
}

foreach ($name in @("COMPLETE", "SHA256SUMS", "manifest.env", "database.dump", "blobs.tar.gz")) {
    if (-not (Test-Path -LiteralPath (Join-Path $backup $name))) {
        throw "Incomplete backup: missing $name"
    }
}

Get-Content -LiteralPath (Join-Path $backup "SHA256SUMS") | ForEach-Object {
    $parts = $_ -split "  ", 2
    if ($parts.Count -ne 2) { throw "Malformed SHA256SUMS entry: $_" }
    $actual = (Get-FileHash -LiteralPath (Join-Path $backup $parts[1]) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $parts[0]) { throw "Checksum mismatch: $($parts[1])" }
}

$manifest = @{}
Get-Content -LiteralPath (Join-Path $backup "manifest.env") | ForEach-Object {
    $parts = $_ -split "=", 2
    if ($parts.Count -eq 2) { $manifest[$parts[0]] = $parts[1] }
}
if (
    $manifest.backup_kind -eq "FULL" -and (
        $manifest.release_material -ne "included-verified" -or
        $manifest.recovery_material -ne "included-encrypted"
    )
) {
    throw "FULL backup must include verified release and encrypted recovery material"
}
if ($manifest.release_material -eq "included-verified") {
    if (-not $env:COOP_RELEASE_PUBLIC_KEY) {
        throw "COOP_RELEASE_PUBLIC_KEY is required to verify backup release"
    }
    $releaseDirectory = Join-Path $backup "release"
    $actualManifestHash = (
        Get-FileHash -LiteralPath (Join-Path $releaseDirectory "release-manifest.json") -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($actualManifestHash -ne $manifest.release_manifest_sha256) {
        throw "Backup release manifest hash does not match manifest.env"
    }
    $verification = @(
        (Join-Path $PSScriptRoot "release_bundle.py")
        "verify"
        "--bundle"
        $releaseDirectory
        "--public-key"
        $env:COOP_RELEASE_PUBLIC_KEY
        "--expected-release"
        $manifest.release
    )
    if ($env:COOP_RELEASE_LICENSE_POLICY_SHA256) {
        $verification += @(
            "--expected-policy-sha256",
            $env:COOP_RELEASE_LICENSE_POLICY_SHA256
        )
    }
    & python @verification | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Backup release verification failed" }
}

try {
    Invoke-Docker run --rm -v "${backup}:/backup:ro" postgres:18-alpine sh -ec "pg_restore --list /backup/database.dump >/dev/null; tar -tzf /backup/blobs.tar.gz >/dev/null"
    Invoke-Docker network create $network | Out-Null
    Invoke-Docker volume create $dbVolume | Out-Null
    Invoke-Docker volume create $blobVolume | Out-Null
    Invoke-Docker run -d --name $container --network $network `
        -e "POSTGRES_USER=coop_migrator" `
        -e "POSTGRES_PASSWORD=$password" `
        -e "POSTGRES_DB=restore_drill" `
        -v "${dbVolume}:/var/lib/postgresql" postgres:18-alpine | Out-Null

    $readyChecks = 0
    foreach ($attempt in 1..60) {
        & docker exec $container pg_isready -U coop_migrator -d restore_drill | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $readyChecks += 1
            if ($readyChecks -ge 3) { break }
        }
        else {
            $readyChecks = 0
        }
        Start-Sleep -Seconds 1
    }
    if ($readyChecks -lt 3) { throw "Restore drill PostgreSQL did not become stable" }

    Invoke-Docker exec $container psql -U coop_migrator -d restore_drill -v ON_ERROR_STOP=1 -c "CREATE ROLE coop_app NOLOGIN"

    $dump = Join-Path $backup "database.dump"
    $restore = Start-Process -FilePath "docker" -ArgumentList @(
        "exec", "-i", $container, "pg_restore",
        "-U", "coop_migrator", "-d", "restore_drill",
        "--exit-on-error", "--no-owner"
    ) -NoNewWindow -Wait -PassThru -RedirectStandardInput $dump
    if ($restore.ExitCode -ne 0) { throw "Database restore failed" }

    $schema = (& docker exec $container psql -U coop_migrator -d restore_drill -Atc "select version_num from alembic_version").Trim()
    if ($LASTEXITCODE -ne 0) { throw "Schema inspection failed" }
    $tableCount = (& docker exec $container psql -U coop_migrator -d restore_drill -Atc "select count(*) from information_schema.tables where table_schema not in ('pg_catalog','information_schema')").Trim()
    if ($LASTEXITCODE -ne 0) { throw "Table inspection failed" }
    $eventCount = (& docker exec $container psql -U coop_migrator -d restore_drill -Atc "select count(*) from journal.signed_events").Trim()
    if ($LASTEXITCODE -ne 0) { throw "Journal inspection failed" }

    Invoke-Docker run --rm -v "${blobVolume}:/target" -v "${backup}:/backup:ro" postgres:18-alpine sh -ec "tar -C /target -xzf /backup/blobs.tar.gz"
    $archiveFiles = (& docker run --rm -v "${backup}:/backup:ro" postgres:18-alpine sh -ec 'tar -tzf /backup/blobs.tar.gz | awk ''!/\/$/ {count++} END {print count+0}''').Trim()
    if ($LASTEXITCODE -ne 0) { throw "Blob archive inspection failed" }
    $restoredFiles = (& docker run --rm -v "${blobVolume}:/target:ro" postgres:18-alpine sh -ec "find /target -type f | wc -l").Trim()
    if ($LASTEXITCODE -ne 0) { throw "Blob restore inspection failed" }
    if ([int] $archiveFiles -ne [int] $restoredFiles) {
        throw "Blob restore file count mismatch: archive=$archiveFiles restored=$restoredFiles"
    }

    "restore_drill=PASS schema=$schema tables=$tableCount events=$eventCount blob_files=$restoredFiles"
}
finally {
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    & docker rm -f $container 2>$null | Out-Null
    & docker network rm $network 2>$null | Out-Null
    & docker volume rm $dbVolume $blobVolume 2>$null | Out-Null
    $ErrorActionPreference = $previousErrorAction
}
