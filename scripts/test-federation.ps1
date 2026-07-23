$ErrorActionPreference = "Stop"

$composeFile = "compose.federation-test.yaml"
$status = 0

try {
    & docker compose -f $composeFile down --volumes --remove-orphans
    if ($LASTEXITCODE -ne 0) { throw "Unable to clean the federation test stack" }

    & docker compose -f $composeFile up --detach --build --wait node-a node-b node-c
    if ($LASTEXITCODE -ne 0) { throw "Unable to start the federation test nodes" }

    & docker compose -f $composeFile run --rm --no-deps acceptance
    if ($LASTEXITCODE -ne 0) { throw "Federation acceptance failed" }
}
catch {
    $status = 1
    Write-Warning $_
    & docker compose -f $composeFile ps --all
    & docker compose -f $composeFile logs --no-color --tail 250
}
finally {
    if ($env:KEEP_FEDERATION_TEST_STACK -ne "1") {
        & docker compose -f $composeFile down --volumes --remove-orphans
    }
}

exit $status
