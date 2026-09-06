"""Drive the phone's REAL screens by their text, one verb per call, so a
rehearsal can be walked step by step and every step read back:

  phone_ui.py dump                      texts on screen, with bounds
  phone_ui.py shot <name>               screenshot to the join-door-shots dir
  phone_ui.py tap "<text>"              tap the first node whose text/desc
                                        contains <text> (case-insensitive);
                                        prefix with = for an exact match
  phone_ui.py type "<text>"             type into the focused field
  phone_ui.py field <n> "<text>"        tap the n-th EditText, type into it
  phone_ui.py signin <email> <password> the sign-in form, keyboard hidden
                                        before the button (see phone_join_door)
  phone_ui.py back                      BACK -- closes the keyboard if shown,
                                        otherwise navigates back
  phone_ui.py launch                    bring the app to the front

Reuses phone_join_door's adb/uiautomator helpers (MIUI retry, utf-8, the
keyboard guard). Read-only unless you tap.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import phone_join_door as d  # noqa: E402

SHOTS = Path(r"C:\Users\MSI\.claude\jobs\215b2785\tmp\join-door-shots\rehearsal")


def dump():
    for text, desc, cls, bounds in d.ui_nodes():
        label = text or desc
        if label:
            short = cls.rsplit(".", 1)[-1]
            print(f"{label!r:60s} {short:12s} {bounds}")


def main():
    verb = sys.argv[1] if len(sys.argv) > 1 else "dump"
    args = sys.argv[2:]
    if verb == "dump":
        dump()
    elif verb == "shot":
        SHOTS.mkdir(parents=True, exist_ok=True)
        out = SHOTS / f"{args[0]}.png"
        raw = subprocess_screencap()
        out.write_bytes(raw)
        print("saved", out, len(raw), "bytes")
    elif verb == "tap":
        wanted = args[0]
        # Bottom-nav labels appear twice in a dump: a zero-size TextView and
        # the tappable item that carries the same text/desc with real bounds.
        # Never tap a zero-area node (that is a tap at 0,0).
        def visible(n):
            b = n[3]
            return bool(b) and (b[2] - b[0]) > 0 and (b[3] - b[1]) > 0
        if wanted.startswith("="):
            exact = wanted[1:].strip().lower()
            node = d.find(lambda n: visible(n) and ((n[0] or "").strip().lower() == exact
                                                    or (n[1] or "").strip().lower() == exact),
                          timeout=15, what=wanted)
        else:
            node = d.find(lambda n: visible(n) and d.by_text(wanted)(n), timeout=15, what=wanted)
        d.tap(node)
        time.sleep(1.0)
        print("tapped", repr(node[0] or node[1]), node[3])
        dump()
    elif verb == "tapxy":
        # For the bottom nav, whose items expose no text or desc with real
        # bounds to uiautomator on this build (labels dump at 0,0): tap by
        # screen coordinates read off a screenshot (1080x2340 on this phone).
        d.adb("shell", "input", "tap", args[0], args[1])
        time.sleep(1.2)
        print("tapped", args[0], args[1])
        dump()
    elif verb == "type":
        d.adb("shell", "input", "text", args[0].replace(" ", "%s"))
        print("typed")
    elif verb == "field":
        fields = d.edit_texts()
        d.type_into(fields[int(args[0])], args[1])
        print("typed into field", args[0])
    elif verb == "signin":
        d.OWNER = {"email": args[0], "password": args[1]}
        d.sign_in("sign-in")
        dump()
    elif verb == "back":
        d.adb("shell", "input", "keyevent", "4")
        time.sleep(1.0)
        dump()
    elif verb == "launch":
        d.adb("shell", "monkey", "-p", d.PKG, "-c", "android.intent.category.LAUNCHER", "1", check=False)
        time.sleep(5)
        dump()
    else:
        sys.exit(f"unknown verb {verb!r}")


def subprocess_screencap():
    import subprocess
    r = subprocess.run([d.ADB, "exec-out", "screencap", "-p"], capture_output=True, timeout=60)
    return r.stdout


if __name__ == "__main__":
    main()
