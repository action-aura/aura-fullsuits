"""Auto-rebuild AuraRetail.exe whenever retail/commercial_runtime source
changes -- keeps presentation_package/AuraRetail/AuraRetail.exe always
current for manual testing without needing a manual rebuild step.

Run: .venv/Scripts/python.exe scripts/watch_and_build_exe.py
Stop: Ctrl+C.

Polls (no extra deps like watchdog needed) rather than using filesystem
events -- simplest thing that works reliably across the mix of tracked
and untracked-but-relevant files (e.g. trust_anchor.json) this build
depends on, and a 3s poll interval is imperceptible for a human editing
code.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATCH_PATHS = [
    ROOT / "products" / "retail" / "backend",
    ROOT / "products" / "retail" / "frontend",
    ROOT / "products" / "retail" / "desktop",
    ROOT / "products" / "retail" / "packaging",
    ROOT / "commercial_runtime",
]
WATCH_EXTRA_FILES = [
    ROOT / "commercial_runtime" / "licensing_contracts" / "trust_anchor.json",
]
DIST_PATH = ROOT / "presentation_package"
WORK_PATH = ROOT / "build_pyinstaller"
SPEC = ROOT / "products" / "retail" / "packaging" / "aura_retail.spec"
BUILD_INFO = DIST_PATH / "BUILD_INFO.txt"
LOG_FILE = ROOT / "scripts" / "watch_and_build_exe.log"
POLL_SECONDS = 3
DEBOUNCE_SECONDS = 5
IGNORE_SUFFIXES = {".pyc"}
IGNORE_DIR_NAMES = {"__pycache__", ".pytest_cache"}

PY = sys.executable


def _iter_watched_files():
    for base in WATCH_PATHS:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_dir():
                continue
            if p.suffix in IGNORE_SUFFIXES:
                continue
            if any(part in IGNORE_DIR_NAMES for part in p.parts):
                continue
            yield p
    for p in WATCH_EXTRA_FILES:
        if p.exists():
            yield p


def compute_signature() -> str:
    h = hashlib.sha256()
    for p in sorted(_iter_watched_files(), key=lambda x: str(x)):
        try:
            stat = p.stat()
        except OSError:
            continue
        h.update(str(p).encode("utf-8", "ignore"))
        h.update(str(stat.st_mtime_ns).encode())
        h.update(str(stat.st_size).encode())
    return h.hexdigest()


def _git_short_hash() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, timeout=10,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT,
            capture_output=True, text=True, timeout=10,
        )
        h = out.stdout.strip() or "unknown"
        if dirty.stdout.strip():
            h += "-dirty"
        return h
    except Exception:
        return "unknown"


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def build() -> bool:
    log("Change detected -- rebuilding AuraRetail.exe...")
    started = time.time()
    result = subprocess.run(
        [
            PY, "-m", "PyInstaller", str(SPEC), "--noconfirm",
            "--distpath", str(DIST_PATH), "--workpath", str(WORK_PATH),
        ],
        cwd=ROOT, capture_output=True, text=True,
    )
    elapsed = time.time() - started
    if result.returncode != 0:
        log(f"BUILD FAILED ({elapsed:.1f}s). Last 40 lines of output:")
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-40:])
        log(tail)
        BUILD_INFO.write_text(
            f"BUILD FAILED at {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"git: {_git_short_hash()}\n"
            f"See scripts/watch_and_build_exe.log for the error.\n",
            encoding="utf-8",
        )
        return False

    BUILD_INFO.write_text(
        f"Built: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"git: {_git_short_hash()}\n"
        f"Build time: {elapsed:.1f}s\n",
        encoding="utf-8",
    )
    log(f"Build OK ({elapsed:.1f}s). AuraRetail.exe refreshed -- git {_git_short_hash()}.")
    return True


def main() -> None:
    log("Watching for changes... (Ctrl+C to stop)")
    last_sig = compute_signature()
    build()  # always build once on startup so the exe matches HEAD right now
    stable_sig = None
    stable_since = None

    while True:
        time.sleep(POLL_SECONDS)
        sig = compute_signature()
        if sig == last_sig:
            continue
        # signature changed since last build -- wait for it to stop
        # changing (debounce) before triggering a build, so a rapid
        # multi-file edit only triggers one rebuild, not several.
        if sig != stable_sig:
            stable_sig = sig
            stable_since = time.time()
            continue
        if time.time() - stable_since >= DEBOUNCE_SECONDS:
            if build():
                last_sig = sig
            else:
                last_sig = sig  # don't retry-loop on a real build error
            stable_sig = None
            stable_since = None


if __name__ == "__main__":
    main()
