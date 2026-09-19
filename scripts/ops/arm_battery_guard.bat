@echo off
REM Arm the battery guard. Double-click this, or run it from a terminal.
REM
REM WHY THIS FILE EXISTS. battery_guard.ps1 has to be STARTED by a human, and
REM that turns out to be a real constraint rather than a convenience gap:
REM an assistant session working in a git worktree cannot spawn it. The
REM worktree-isolation guard refuses to launch powershell.exe or cmd.exe at
REM all (it cannot prove the shell text is not a git command aimed outside the
REM worktree), and anything started as a session-scoped background job dies
REM with that session -- which is the opposite of what a guard that must
REM outlive everything needs.
REM
REM The result, measured 2026-09-18: the guard had been DEAD since
REM 2026-09-16T03:04 and nobody noticed, because a dead guard looks exactly
REM like a guard that has had nothing to do. Hence one double-click.
REM
REM Safe to run twice: it checks for a live guard first and will not start a
REM second one. PowerShell reads the script once at launch, so after editing
REM battery_guard.ps1's threshold you must stop the old process and re-run
REM this -- the file's own header says the same thing.

setlocal
set "GUARD=%~dp0battery_guard.ps1"

if not exist "%GUARD%" (
  echo [battery guard] ERROR: battery_guard.ps1 not found next to this file.
  echo                 Looked for: "%GUARD%"
  pause
  exit /b 1
)

echo [battery guard] Checking whether one is already running...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$running = Get-CimInstance Win32_Process -Filter \"Name='powershell.exe' OR Name='pwsh.exe'\" | Where-Object { $_.CommandLine -like '*battery_guard.ps1*' };" ^
  "if ($running) { Write-Host '[battery guard] Already running (PID ' $running.ProcessId '). Nothing to do.'; exit 0 }" ^
  "Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','%GUARD%' -WindowStyle Hidden;" ^
  "Start-Sleep -Seconds 2;" ^
  "$now = Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | Where-Object { $_.CommandLine -like '*battery_guard.ps1*' };" ^
  "if ($now) { Write-Host '[battery guard] Started. PID' $now.ProcessId '- log:' $env:TEMP'\battery_guard.log' } else { Write-Host '[battery guard] FAILED to start.'; exit 1 }"

echo.
echo [battery guard] Threshold is whatever battery_guard.ps1 says (currently 15%%).
echo [battery guard] Verify any time with:  type %%TEMP%%\battery_guard.log
echo.
pause
