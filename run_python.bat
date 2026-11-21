@echo off
title KDC DICOM Print Server
cd /d "%~dp0"
:loop
python kdc_print_server.py
echo Server stopped. Restarting in 5 seconds (close this window to stop)...
timeout /t 5 >nul
goto loop
