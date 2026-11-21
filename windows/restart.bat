@echo off
REM Restart the server after editing config.ini
taskkill /im KDCPrintServer.exe /f >nul 2>&1
timeout /t 2 >nul
schtasks /run /tn "KDC DICOM Print Server" >nul 2>&1 || start "" "%~dp0KDCPrintServer.exe"
echo Server restarted.
timeout /t 3 >nul
