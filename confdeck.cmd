@echo off
set "CONFDECK_BIN=%~dp0vendor\bin"
if exist "%~dp0runtime\python.exe" (
  "%~dp0runtime\python.exe" -m confdeck %*
) else (
  "%~dp0.venv\Scripts\python.exe" -m confdeck %*
)
