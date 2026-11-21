@echo off
REM Right-click > "Run as administrator".
taskkill /im KDCPrintServer.exe /f >nul 2>&1
schtasks /delete /tn "KDC DICOM Print Server" /f >nul 2>&1
netsh advfirewall firewall delete rule name="KDC DICOM Print Server" >nul 2>&1
echo KDC DICOM Print Server stopped and removed from startup.
echo You can now delete this folder.
pause
