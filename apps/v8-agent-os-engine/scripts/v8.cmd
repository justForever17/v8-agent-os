@echo off
setlocal
set SCRIPT_DIR=%~dp0
set ENGINE_PY=%SCRIPT_DIR%..\.venv\Scripts\python.exe
if exist "%ENGINE_PY%" (
  "%ENGINE_PY%" "%SCRIPT_DIR%v8.py" %*
) else (
  python "%SCRIPT_DIR%v8.py" %*
)
