@echo off
cd /d "%~dp0"
echo Starting ConfDeck Live Webcam Validator...

:: Check which python environment has cv2 installed
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import cv2" >nul 2>&1
  if %errorlevel% equ 0 (
    ".venv\Scripts\python.exe" scripts\test_webcam_vvad.py
    goto :done
  )
)

python -c "import cv2" >nul 2>&1
if %errorlevel% equ 0 (
  set "PYTHONPATH=%~dp0"
  python scripts\test_webcam_vvad.py
  goto :done
)

if exist "runtime\python.exe" (
  "runtime\python.exe" scripts\test_webcam_vvad.py
  goto :done
)

echo ERROR: Could not find Python with opencv-python (cv2) installed.
echo Run: pip install opencv-python

:done
pause
