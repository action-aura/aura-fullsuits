@echo off
cd /d "%~dp0"
echo Aura Retail auto-rebuild watcher starting...
echo Keeps presentation_package\AuraRetail\AuraRetail.exe always in sync with source.
echo Leave this window open. Close it (or Ctrl+C) to stop.
echo.
".venv\Scripts\python.exe" "scripts\watch_and_build_exe.py"
pause
