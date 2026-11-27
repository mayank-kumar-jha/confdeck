@echo off
cd /d "%~dp0"
set "CONFDECK_BIN=%~dp0vendor\bin"
if exist "runtime\python.exe" (
  "runtime\python.exe" -m confdeck start %*
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m confdeck start %*
) else (
  echo Install the offline bundle first. See README.md.
)
pause
