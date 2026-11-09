@echo off
setlocal enabledelayedexpansion
title RAG Knowledge Base - dev stack

rem ---------------------------------------------------------------
rem  One click start for the RAG knowledge base dev stack.
rem  Starts postgres + redis when needed, applies migrations, then
rem  opens one console window per service and waits for health.
rem  This script never kills processes and never frees ports.
rem ---------------------------------------------------------------

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "API_PORT=8000"
set "WEB_PORT=5173"
set "PG_PORT=5432"
set "REDIS_PORT=6379"
set "API_URL=http://127.0.0.1:%API_PORT%"
set "WEB_URL=http://127.0.0.1:%WEB_PORT%"
set "HEALTH_URL=%API_URL%/readyz"

echo ============================================================
echo   RAG knowledge base - one click start
echo   project: %ROOT%
echo ============================================================
echo.

cd /d "%ROOT%"
if errorlevel 1 goto :fail_cd

rem ---- locate uv ----
where uv >nul 2>nul
if not errorlevel 1 goto :uv_ready
if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 goto :missing_uv
:uv_ready

rem ---- locate node ----
rem node is often installed outside PATH. check the usual places first,
rem then fall back to the bundled runtime under the user profile.
set "NODE_EXE="
where node >nul 2>nul
if not errorlevel 1 for /f "delims=" %%p in ('where node') do if not defined NODE_EXE set "NODE_EXE=%%p"

if defined NODE_EXE goto :node_ready
if exist "%ProgramFiles%\nodejs\node.exe" set "NODE_EXE=%ProgramFiles%\nodejs\node.exe"
if defined NODE_EXE goto :node_ready
if exist "%ProgramFiles(x86)%\nodejs\node.exe" set "NODE_EXE=%ProgramFiles(x86)%\nodejs\node.exe"
if defined NODE_EXE goto :node_ready
if exist "%LOCALAPPDATA%\Programs\nodejs\node.exe" set "NODE_EXE=%LOCALAPPDATA%\Programs\nodejs\node.exe"
if defined NODE_EXE goto :node_ready
for /d %%d in ("%USERPROFILE%\.workbuddy\binaries\node\versions\*") do if not defined NODE_EXE if exist "%%~d\node.exe" set "NODE_EXE=%%~d\node.exe"
if not defined NODE_EXE goto :missing_node

:node_ready
for %%d in ("%NODE_EXE%") do set "NODE_DIR=%%~dpd"
if "%NODE_DIR:~-1%"=="\" set "NODE_DIR=%NODE_DIR:~0,-1%"
set "PATH=%NODE_DIR%;%PATH%"
where npm >nul 2>nul
if errorlevel 1 goto :missing_npm
echo [i] node found: %NODE_EXE%

where curl.exe >nul 2>nul
if errorlevel 1 goto :missing_curl

set "DOCKER_MISSING="
where docker >nul 2>nul
if errorlevel 1 set "DOCKER_MISSING=1"

if not exist "%ROOT%\.env" goto :create_env
goto :check_python_deps

:create_env
if not exist "%ROOT%\.env.example" goto :missing_env
copy /y "%ROOT%\.env.example" "%ROOT%\.env" >nul
if errorlevel 1 goto :env_copy_failed
echo [i] .env created from .env.example
echo [i] providers default to fake unless you fill in API keys

:check_python_deps
if exist "%ROOT%\.venv\Scripts\python.exe" goto :check_web_deps
echo [..] python virtual env not found, running uv sync
uv sync
if errorlevel 1 goto :sync_failed
echo [i] python dependencies ready

:check_web_deps
if exist "%ROOT%\web\node_modules" goto :check_api_port
echo [..] web node_modules not found, running npm install
pushd "%ROOT%\web"
npm install --no-audit --no-fund
set "NPM_RC=%errorlevel%"
popd
if not "%NPM_RC%"=="0" goto :npm_failed
echo [i] web dependencies ready

:check_api_port
call :port_pid %API_PORT%
if not defined PORT_PID goto :api_port_free
echo [i] port %API_PORT% is already in use, PID !PORT_PID!
curl.exe -fsS --noproxy "*" -o NUL "%HEALTH_URL%" >nul 2>nul
if errorlevel 1 goto :port_conflict
echo [i] existing API is healthy, API and worker will not be started again
set "START_API=0"
goto :check_web_port

:api_port_free
set "START_API=1"

:check_web_port
call :port_pid %WEB_PORT%
if not defined PORT_PID goto :web_port_free
echo [i] port %WEB_PORT% is already in use, PID !PORT_PID!
echo [i] web dev server will not be started again
set "START_WEB=0"
goto :ensure_infra

:web_port_free
set "START_WEB=1"

:ensure_infra
call :port_pid %PG_PORT%
if not defined PORT_PID goto :start_infra
call :port_pid %REDIS_PORT%
if not defined PORT_PID goto :start_infra
echo [i] postgres and redis are already listening
goto :infra_ready

:start_infra
if defined DOCKER_MISSING goto :docker_required
echo [..] starting postgres and redis via docker compose
docker compose up -d postgres redis
if errorlevel 1 goto :docker_failed

set /a INFRA_TRIES=0
:wait_infra_loop
call :port_pid %PG_PORT%
if not defined PORT_PID goto :wait_infra_next
call :port_pid %REDIS_PORT%
if defined PORT_PID goto :infra_ready
:wait_infra_next
set /a INFRA_TRIES+=1
if %INFRA_TRIES% GEQ 30 goto :infra_timeout
timeout /t 2 /nobreak >nul
goto :wait_infra_loop

:infra_ready
echo [..] applying database migrations
uv run alembic upgrade head
if errorlevel 1 goto :migrate_failed
echo [i] database schema is up to date

:launch
if "%START_API%"=="0" goto :launch_web
ver >nul
start "RAG API" /D "%ROOT%" "%ComSpec%" /k "uv run uvicorn app.main:app --host 127.0.0.1 --port %API_PORT%"
if errorlevel 1 goto :start_failed
ver >nul
start "RAG Worker" /D "%ROOT%" "%ComSpec%" /k "uv run python -m worker.main"
if errorlevel 1 goto :start_failed
echo [i] API and worker windows started

:launch_web
if "%START_WEB%"=="0" goto :wait_health
ver >nul
start "RAG Web" /D "%ROOT%\web" "%ComSpec%" /k "npm run dev -- --host 127.0.0.1 --port %WEB_PORT%"
if errorlevel 1 goto :start_failed
echo [i] web dev server window started

:wait_health
echo [..] waiting for API readiness at %HEALTH_URL%
set /a HEALTH_TRIES=0
:wait_health_loop
curl.exe -fsS --noproxy "*" -o NUL "%HEALTH_URL%" >nul 2>nul
if not errorlevel 1 goto :healthy
set /a HEALTH_TRIES+=1
if %HEALTH_TRIES% GEQ 60 goto :health_timeout
timeout /t 2 /nobreak >nul
goto :wait_health_loop

:healthy
echo [i] API readiness check passed
echo.
echo   Web console : %WEB_URL%
echo   API docs    : %API_URL%/docs
echo   Health      : %HEALTH_URL%
echo.
echo   Each service runs in its own window. Close the window to stop it.
echo.
start "" "%WEB_URL%"
echo [i] browser opened. Press any key to close this launcher window.
if "%RAG_START_NOPAUSE%"=="1" exit /b 0
pause
exit /b 0

rem ---------------------------------------------------------------
rem  Failure paths
rem ---------------------------------------------------------------

:fail_cd
echo [x] cannot switch to project directory: %ROOT%
goto :fail

:missing_uv
echo [x] uv is not installed or not on PATH.
echo     install: https://docs.astral.sh/uv/getting-started/installation/
goto :fail

:missing_node
echo [x] node was not found in PATH or in the usual install locations.
echo     install node 20 or newer: https://nodejs.org/en/download
echo     then run this script again.
goto :fail

:missing_npm
echo [x] node was found at %NODE_EXE% but npm was not found next to it.
echo     reinstall node with the official installer so npm is included.
goto :fail

:missing_curl
echo [x] curl.exe is not available. Windows 10 1803 or newer ships it.
goto :fail

:missing_env
echo [x] missing .env and .env.example in %ROOT%
goto :fail

:env_copy_failed
echo [x] failed to create .env from .env.example
goto :fail

:sync_failed
echo [x] uv sync failed. check the output above and the python version in .python-version
goto :fail

:npm_failed
echo [x] npm install failed. check network and registry settings in web\.npmrc
goto :fail

:docker_required
echo [x] docker is not on PATH but postgres or redis is not listening.
echo     start Docker Desktop, or run your own postgres on port %PG_PORT% and redis on port %REDIS_PORT%
goto :fail

:docker_failed
echo [x] docker compose up failed. is Docker Desktop running?
goto :fail

:infra_timeout
echo [x] postgres or redis did not start listening within 60 seconds
goto :fail

:migrate_failed
echo [x] alembic upgrade failed. check DATABASE_URL in .env and that postgres is reachable
goto :fail

:start_failed
echo [x] failed to open a service window
goto :fail

:health_timeout
echo [x] API did not become ready within 120 seconds
echo     look at the RAG API window for the error, then run this script again
goto :fail

:port_conflict
echo [x] port %API_PORT% is used by PID !PORT_PID! but the health check failed.
echo     another program holds the port. free it with:
echo         taskkill /PID !PORT_PID! /F
echo     then run this script again.
goto :fail

:fail
echo.
echo Startup aborted.
if "%RAG_START_NOPAUSE%"=="1" exit /b 1
pause
exit /b 1

rem ---------------------------------------------------------------
rem  Helper: put the PID listening on the given port into PORT_PID.
rem  Matches the trailing space so 8000 does not match 80001,
rem  and matches both IPv4 and [::] IPv6 listen lines.
rem ---------------------------------------------------------------
:port_pid
set "PORT_PID="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":%~1 "') do set "PORT_PID=%%p"
goto :eof
