"""Phone themes, on the handset: install the current build, sign in as the demo
cashier, open Settings -> Theme, pick each theme in turn, screenshot the
Products screen, then put Calm back and log out. Written 2026-09-07 while
the phone was unplugged; run it on the next plug-in:

    python scripts/ops/phone_theme_check.py

Reads every screen back through uiautomator (see phone_ui.py / phone_join_door.py).
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import phone_join_door as d  # noqa: E402
import phone_ui as ui  # noqa: E402

APK = HERE.parents[1] / "android" / "aura-retail" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
CASHIER = {"email": "cashier.demo@rehearsal.local", "password": "DemoCashier2026!"}
THEMES = ["Day", "Sand", "Night", "Dusk", "Calm"]   # ends on Calm, the phone's default
SHOTS = Path(r"C:\Users\MSI\.claude\jobs\215b2785\tmp\join-door-shots\themes-phone")


def texts():
    return [t or c for t, c, _, _ in d.ui_nodes() if t or c]


def tap_text(label, exact=True):
    node = d.find(lambda n: ((n[0] or "").strip() == label) if exact else (label.lower() in (n[0] or "").lower()),
                  timeout=20, what=label)
    d.tap(node)


def shot(name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([d.ADB, "exec-out", "screencap", "-p"], capture_output=True, timeout=60)
    (SHOTS / f"{name}.png").write_bytes(r.stdout)


def main():
    if not any("\tdevice" in l for l in d.adb("devices").splitlines()):
        sys.exit("no authorised device")
    print("installing", APK.name, "...")
    print(subprocess.run([d.ADB, "install", "-r", str(APK)], capture_output=True, text=True, timeout=300).stdout.strip().splitlines()[-1])
    d.adb("reverse", "tcp:5551", "tcp:5551", check=False)
    d.adb("shell", "monkey", "-p", d.PKG, "-c", "android.intent.category.LAUNCHER", "1", check=False)
    time.sleep(6)
    if any(t.strip().lower() == "sign in" for t in texts()):
        d.OWNER = CASHIER
        d.sign_in("cashier sign-in")
        time.sleep(2)
    for theme in THEMES:
        d.adb("shell", "input", "tap", "980", "2223")      # More (LTR)
        time.sleep(1.2)
        d.adb("shell", "input", "swipe", "540", "1900", "540", "500", "300")
        time.sleep(1.0)
        tap_text("Settings")
        tap_text("Theme", exact=True)
        tap_text(theme)
        time.sleep(0.8)
        seen = [t for t in texts() if t in THEMES]
        print(f"picked {theme}; Settings now shows: {seen[:2]}")
        tap_text("Back")
        d.adb("shell", "input", "tap", "318", "2223")      # Products (LTR)
        time.sleep(1.5)
        shot(f"phone-{theme.lower()}-products")
        print(f"  screenshot {theme.lower()}: {SHOTS / (f'phone-{theme.lower()}-products.png')}")
    d.adb("shell", "input", "tap", "980", "2223")
    time.sleep(1.2)
    d.adb("shell", "input", "swipe", "540", "1900", "540", "500", "300")
    time.sleep(1.0)
    tap_text("Log out")
    print("RESULT:", "THEMES WALKED ON THE PHONE, LEFT ON CALM AT SIGN-IN" if any("Sign in" in t for t in texts()) else "NOT YET")


if __name__ == "__main__":
    main()
