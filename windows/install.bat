@echo off
REM KDC DICOM Print Server - install (exe version, no Python needed)
REM Right-click > "Run as administrator".
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
  echo Please right-click install.bat and choose "Run as administrator".
  pause
  exit /b 1
)

if not exist "%~dp0KDCPrintServer.exe" (
  echo KDCPrintServer.exe not found in this folder.
  pause
  exit /b 1
)

set PORT=104
for /f "tokens=2 delims==" %%p in ('findstr /b /c:"port" config.ini') do set PORT=%%p
set PORT=%PORT: =%

echo Opening Windows Firewall for TCP port %PORT%...
netsh advfirewall firewall delete rule name="KDC DICOM Print Server" >nul 2>&1
netsh advfirewall firewall add rule name="KDC DICOM Print Server" dir=in action=allow protocol=TCP localport=%PORT%
netsh advfirewall firewall add rule name="KDC DICOM Print Server" dir=in action=allow program="%~dp0KDCPrintServer.exe" >nul 2>&1

echo Setting the server to start automatically when %USERNAME% logs in...
schtasks /delete /tn "KDC DICOM Print Server" /f >nul 2>&1
schtasks /create /tn "KDC DICOM Print Server" /tr "\"%~dp0KDCPrintServer.exe\"" /sc onlogon /rl highest /f
if errorlevel 1 ( echo Could not create the startup task. & pause & exit /b 1 )

echo Starting the server now...
taskkill /im KDCPrintServer.exe /f >nul 2>&1
schtasks /run /tn "KDC DICOM Print Server" >nul

echo.
echo Installed. The server runs in the background (no window).
echo Activity is logged in print_server.log in this folder.
echo Run TestPrint.exe to print a test film.
pause
