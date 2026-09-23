@echo off
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"
set "PYTHONPATH=%APP_DIR%"
"D:\software\anaconda3\python.exe" -B "%APP_DIR%preview_recommendation.py"
pause
