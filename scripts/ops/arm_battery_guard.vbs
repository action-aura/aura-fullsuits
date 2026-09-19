' Arms the battery guard, hidden, at every logon.
'
' WHY THIS FILE EXISTS AT ALL
' The owner has asked four times since 2026-09-05 for the guard to be armed,
' and it kept not staying armed. The log shows it last ticked at
' 2026-09-16T03:04 and then stopped, which is the shape of the real problem:
' every previous attempt started the guard as a CHILD of an assistant session,
' so it died the moment that session did -- silently, while the Task Manager
' entry it left behind made it look armed.
'
' A Scheduled Task would be the tidier answer and was tried first:
' `schtasks /Create` returns "Access is denied" without elevation on this
' machine. The Startup folder needs no elevation and survives reboots, which
' is the property that actually matters here.
'
' WScript.Shell.Run with intWindowStyle 0 is what keeps it hidden. A .bat in
' Startup would work too and would flash a console window at every logon,
' which the owner would reasonably read as something being wrong.
'
' The guard script itself lives OUTSIDE the repo on purpose
' (~/.claude/ops/battery_guard.ps1). It used to be referenced inside a git
' worktree, which is deleted when its session is cleaned up -- so the arming
' would have quietly started pointing at nothing, and would have failed in
' exactly the same invisible way as before. The repo copy at
' scripts/ops/battery_guard.ps1 remains the source of truth to EDIT; this one
' is the copy that RUNS. Re-copy it after changing the threshold, and restart
' the guard: PowerShell reads a script once, at launch, so an edit does not
' reach a process that is already running.

Option Explicit

Dim sh, guard
Set sh = CreateObject("WScript.Shell")
guard = sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\.claude\ops\battery_guard.ps1"

sh.Run "powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -NoProfile -File """ & guard & """", 0, False
