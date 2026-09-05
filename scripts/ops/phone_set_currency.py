"""Set the phone's shop currency to JOD.

The retail_settings row is per install (settings are not a synced entity),
the only route that writes it needs an admin session, and the phone's admin
password is the owner's, not ours. So: stop the app, pull retail.db with its
WAL/SHM, checkpoint + UPDATE locally, push the merged .db back and drop the
stale WAL/SHM on the device, relaunch. Prints before/after read from the
device itself.
"""
import os
import sqlite3
import subprocess
import sys
import time

ADB = r"C:\Users\MSI\AppData\Local\Microsoft\WinGet\Packages\Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe\platform-tools\adb.exe"
PKG = "com.actionaura.retail.debug"
REMOTE = "files/data/database/subsystems/retail.db"
TMP = r"C:\Users\MSI\.claude\jobs\215b2785\tmp\phone_currency"
os.makedirs(TMP, exist_ok=True)
LOCAL = os.path.join(TMP, "retail.db")
TARGET = (sys.argv[1] if len(sys.argv) > 1 else "JOD").upper()


def adb(*args, check=True):
    return subprocess.run([ADB, *args], capture_output=True, text=True, check=check)


def pull(remote, local):
    out = subprocess.run([ADB, "exec-out", f"run-as {PKG} cat {remote}"], capture_output=True, check=False)
    if out.returncode == 0 and out.stdout:
        with open(local, "wb") as fh:
            fh.write(out.stdout)
        return True
    if os.path.exists(local):
        os.remove(local)
    return False


def read_currency():
    for ext in ("", "-wal", "-shm"):
        pull(REMOTE + ext, LOCAL + ext)
    conn = sqlite3.connect(LOCAL)
    try:
        return conn.execute("SELECT company_id, svalue FROM retail_settings WHERE skey='base_currency'").fetchall()
    finally:
        conn.close()


print("stopping app")
stop = adb("shell", "am", "force-stop", PKG, check=False)
print("force-stop exit", stop.returncode, (stop.stdout + stop.stderr).strip()[:160])
time.sleep(2)
alive = adb("shell", "pidof", PKG, check=False).stdout.strip()
if alive:
    sys.exit(f"app still running (pid {alive}); refusing to swap its database underneath it")
print("before:", read_currency())

conn = sqlite3.connect(LOCAL)
conn.execute("PRAGMA journal_mode=DELETE")          # fold WAL into the main file
n = conn.execute("UPDATE retail_settings SET svalue=? WHERE skey='base_currency'", (TARGET,)).rowcount
conn.commit()
print("rows updated locally:", n, "->", conn.execute("SELECT svalue FROM retail_settings WHERE skey='base_currency'").fetchall())
conn.close()
for ext in ("-wal", "-shm"):
    if os.path.exists(LOCAL + ext):
        os.remove(LOCAL + ext)

print("pushing")
adb("push", LOCAL, "/data/local/tmp/retail.db")
adb("shell", f"run-as {PKG} cp /data/local/tmp/retail.db {REMOTE}")
adb("shell", f"run-as {PKG} rm -f {REMOTE}-wal {REMOTE}-shm")
adb("shell", "rm", "-f", "/data/local/tmp/retail.db")
print("after (device):", read_currency())

print("relaunching app")
adb("shell", "monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1", check=False)
