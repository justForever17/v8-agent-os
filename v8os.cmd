@echo off
setlocal
set "V8OS_ROOT=%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo V8OS CLI requires Node.js 20 or newer on PATH.
  exit /b 1
)
node "%V8OS_ROOT%apps\v8-agent-os-cli\bin\v8os.mjs" %*
