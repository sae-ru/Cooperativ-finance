@echo off
setlocal
cd /d "%~dp0"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=demo"

where docker >nul 2>nul
if errorlevel 1 (
  echo Docker Desktop is not installed or is not available in PATH.
  exit /b 1
)

docker compose version >nul 2>nul
if errorlevel 1 (
  echo Docker Compose is not available.
  exit /b 1
)

if /I "%MODE%"=="production" (
  set "COOP_DEMO_DATA_ENABLED=false"
  set "COMPOSE_PROFILES=production"
  powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\bootstrap-node.ps1"
) else (
  if /I not "%MODE%"=="demo" (
    echo Usage: start.bat [demo^|production]
    exit /b 2
  )
  set "COOP_DEMO_DATA_ENABLED=true"
  set "COMPOSE_PROFILES=demo"
  powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\bootstrap-node.ps1" -DemoCredentials
)
if errorlevel 1 exit /b %errorlevel%

docker compose up -d --build
if errorlevel 1 exit /b %errorlevel%

powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\verify-stack.ps1"
if errorlevel 1 exit /b %errorlevel%

echo.
echo Cooperative Clearing is ready: http://127.0.0.1:8080
if /I "%MODE%"=="demo" (
  echo.
  echo Demo accounts for a fresh installation:
  echo   registrar / CoopDemo-Registrar-2026!
  echo   security  / CoopDemo-Security-2026!
  echo   auditor   / CoopDemo-Auditor-2026!
  echo Passwords are requested to be changed after the first sign-in.
)
endlocal