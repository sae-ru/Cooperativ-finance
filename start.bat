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

if /I "%MODE%"=="production" goto production
if /I not "%MODE%"=="demo" goto usage
if not "%~2"=="" goto usage
if exist ".env" findstr /B /C:"COOP_ENVIRONMENT=staging-node" /C:"COOP_ENVIRONMENT=pilot" /C:"COOP_ENVIRONMENT=production" ".env" >nul 2>nul
if not errorlevel 1 goto demo_downgrade_refused

set "COOP_ENVIRONMENT=dev"
set "COOP_DEMO_DATA_ENABLED=true"
set "COMPOSE_PROFILES=demo"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\bootstrap-node.ps1" -DemoCredentials
if errorlevel 1 exit /b %errorlevel%

docker compose up -d --build
if errorlevel 1 exit /b %errorlevel%
goto verify

:production
if not "%~6"=="" goto production_usage
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3 is required to verify a production release bundle.
  exit /b 1
)
set "BUNDLE=%~2"
set "PUBLIC_KEY=%~3"
set "RELEASE=%~4"
set "POLICY_SHA256=%~5"
if "%BUNDLE%"=="" set "BUNDLE=%COOP_VERIFIED_RELEASE_BUNDLE%"
if "%PUBLIC_KEY%"=="" set "PUBLIC_KEY=%COOP_RELEASE_PUBLIC_KEY%"
if "%RELEASE%"=="" set "RELEASE=%COOP_RELEASE%"
if "%POLICY_SHA256%"=="" set "POLICY_SHA256=%COOP_RELEASE_LICENSE_POLICY_SHA256%"
if "%BUNDLE%"=="" goto production_usage
if "%PUBLIC_KEY%"=="" goto production_usage
if "%RELEASE%"=="" goto production_usage
if "%POLICY_SHA256%"=="" goto production_usage

python ".\scripts\release_bundle.py" verify --bundle "%BUNDLE%" --public-key "%PUBLIC_KEY%" --expected-release "%RELEASE%" --expected-policy-sha256 "%POLICY_SHA256%" --load-images
if errorlevel 1 exit /b %errorlevel%

set "COOP_ENVIRONMENT=production"
set "COOP_DEMO_DATA_ENABLED=false"
set "COMPOSE_PROFILES=production"
set "COOP_RELEASE=%RELEASE%"
set "COOP_VERIFIED_RELEASE_BUNDLE=%BUNDLE%"
set "COOP_RELEASE_PUBLIC_KEY=%PUBLIC_KEY%"
set "COOP_RELEASE_LICENSE_POLICY_SHA256=%POLICY_SHA256%"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\bootstrap-node.ps1" -Mode production -Release "%RELEASE%"
if errorlevel 1 exit /b %errorlevel%

docker compose up -d --no-build --pull never
if errorlevel 1 exit /b %errorlevel%

:verify
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
exit /b 0

:demo_downgrade_refused
echo A hardened node cannot be started in demo mode in place.
exit /b 1

:production_usage
echo Production requires a verified bundle, independent public key, release id and approved license-policy SHA-256.
echo Usage: start.bat production ^<bundle-directory^> ^<public-key^> ^<release^> ^<policy-sha256^>
exit /b 2

:usage
echo Usage: start.bat [demo^|production ^<bundle-directory^> ^<public-key^> ^<release^> ^<policy-sha256^>]
exit /b 2
