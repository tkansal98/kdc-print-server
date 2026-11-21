@echo off
REM Run once. Right-click > "Run as administrator" so the firewall rule can be added.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Install Python 3.11+ from python.org
  echo and tick "Add python.exe to PATH" during setup, then run this again.
  pause
  exit /b 1
)

echo Installing Python packages...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 ( echo Package install failed. & pause & exit /b 1 )

for /f "tokens=2 delims==" %%p in ('findstr /b /c:"port" config.ini') do set PORT=%%p
set PORT=%PORT: =%
if "%PORT%"=="" set PORT=104

echo Opening Windows Firewall for TCP port %PORT%...
netsh advfirewall firewall delete rule name="KDC DICOM Print Server" >nul 2>nul
netsh advfirewall firewall add rule name="KDC DICOM Print Server" dir=in action=allow protocol=TCP localport=%PORT%
if errorlevel 1 echo (Firewall rule failed - run this file as administrator.)

echo.
echo Done. Start the server with run_python.bat
pause
