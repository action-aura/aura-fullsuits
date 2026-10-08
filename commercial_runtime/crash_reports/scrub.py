"""Crash reports -- the privacy filter. Pure functions, no I/O, no state.

EVERYTHING that may leave a till passes through this module first, so this is
where the promise "a crash report never carries a customer, an amount, a
licence key or a home folder" is kept or broken. Nothing here reads a
database or a request; it only turns text and tracebacks into the small,
safe shapes the rest of the package stores and sends.

What a report is allowed to say about a crash:
  * the exception CLASS name           (code, not data)
  * a scrubbed, truncated message      (see scrub_message)
  * frames: file (relative), function, line number   -- no source lines, no
    local variables, no absolute paths
  * a fingerprint that groups the same bug together

What it must never say: sales, customers, products, prices, names, the licence
key, the device key, cookies/session, environment variables. The message is
the only free-text field, which is why it gets four blunt rules (and a fifth,
for licence keys) instead of one clever one: blunt over-scrubbing costs a
slightly vaguer message; under-scrubbing costs a customer's data on somebody
else's server.
"""
from __future__ import annotations

import hashlib
import os
import re
import traceback
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional
from urllib.parse import urlsplit

MAX_MESSAGE_CHARS = 300
MAX_FRAMES = 30
# How many frames feed the fingerprint. The frames list is stored INNERMOST
# first (the crash site, then its callers), so "the first 8" are the 8 closest
# to the bug -- the ones that distinguish one bug from another. The outermost
# frames of a Flask request are always the same wsgi/dispatch plumbing and
# would make every server error look alike.
FINGERPRINT_FRAMES = 8
MAX_FIELD_CHARS = 200          # file / function / exception_type, Owner enforces the same ceiling
FINGERPRINT_LENGTH = 32

# The root every frame path is made relative to: the folder holding both
# `commercial_runtime` and `products` (the repo checkout in development, the
# unpacked bundle in the frozen exe). Derived from THIS file's location rather
# than from the working directory, which the launcher is free to change.
APP_ROOT = Path(__file__).resolve().parents[2]

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Licence keys look like AURA-RETAIL-XXXX-... . NOT one of the four rules the
# plan lists: a key is only ever digits-light letters, so the 4-digit rule
# would miss it, and "never the licence key" is the one promise that cannot
# depend on luck. Runs before the digit rule so the whole key goes, not half.
_LICENCE_KEY_RE = re.compile(r"\bAURA-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+", re.IGNORECASE)
_LONG_DIGITS_RE = re.compile(r"\d{4,}")
_SINGLE_QUOTED_RE = re.compile(r"'[^'\r\n]*'")
_DOUBLE_QUOTED_RE = re.compile(r'"[^"\r\n]*"')
# <drive>:\Users\<name>[\...]  and  /home/<name>[/...]  /Users/<name>[/...].
# The user-name segment may contain spaces (Windows allows them), so it runs
# up to the next separator instead of the next space -- otherwise
# "C:\Users\John Smith\x" would leave "Smith" behind.
#
# The tail after the user folder may itself contain spaces ("...\Aura Retail\
# retail.db"): every MIDDLE segment (one followed by another separator) may, and
# only the final segment stops at whitespace. Over-eating the odd word is
# fine; leaving half a path behind is not.
_PATH_TAIL = r"(?:[\\/][^\\/\r\n'\"]*(?=[\\/]))*(?:[\\/][^\s'\"]*)?"
_WIN_HOME_RE = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/:*?\"<>|\r\n]+" + _PATH_TAIL, re.IGNORECASE)
_POSIX_HOME_RE = re.compile(r"(?:/home|/Users)/[^/\s'\"]+" + _PATH_TAIL)
_WHITESPACE_RE = re.compile(r"\s+")


def _literal_home_pattern() -> Optional[re.Pattern]:
    """The actual home folder of the account this process runs as, if it can
    be told. Defence in depth for homes the shape rules above do not cover
    (a redirected profile, D:\\Profiles\\bob). Too short to be meaningful
    (a bare drive or "/") would shred every message, so those are skipped."""
    try:
        home = os.path.expanduser("~")
    except Exception:  # noqa: BLE001 -- a privacy helper must never raise
        return None
    if not home or home == "~" or len(home) < 6:
        return None
    return re.compile(re.escape(home) + _PATH_TAIL, re.IGNORECASE)


_LITERAL_HOME_RE = _literal_home_pattern()


def scrub_message(text: object) -> str:
    """Scrub, then truncate, an exception message. Rules, IN THIS ORDER:

      1. e-mail addresses                      -> "<email>"
      2. licence keys (AURA-...-...)           -> "<key>"      (added, see above)
      3. any run of 4+ digits                  -> "<n>"
         (phones, amounts, ids, barcodes, years)
      4. quoted substrings '...' and "..."     -> "'…'"
         (IntegrityError/ValueError messages quote user data)
      5. user home paths (Windows / posix)     -> "<path>"

    Order matters: e-mails and keys go first because the digit rule would
    otherwise mangle them into unrecognisable halves; the quote rule runs
    after the digit rule so a quoted amount is gone either way. The result is
    capped at 300 characters AFTER scrubbing, so a long message cannot push
    an unscrubbed tail past the cut.
    """
    if text is None:
        return ""
    try:
        value = str(text)
    except Exception:  # noqa: BLE001 -- a __str__ that raises must not break reporting
        return ""
    value = _EMAIL_RE.sub("<email>", value)
    value = _LICENCE_KEY_RE.sub("<key>", value)
    value = _LONG_DIGITS_RE.sub("<n>", value)
    value = _SINGLE_QUOTED_RE.sub("'…'", value)
    value = _DOUBLE_QUOTED_RE.sub("'…'", value)
    value = _WIN_HOME_RE.sub("<path>", value)
    value = _POSIX_HOME_RE.sub("<path>", value)
    if _LITERAL_HOME_RE is not None:
        value = _LITERAL_HOME_RE.sub("<path>", value)
    value = _WHITESPACE_RE.sub(" ", value).strip()
    return value[:MAX_MESSAGE_CHARS]


def _clip(value: object) -> str:
    return str(value)[:MAX_FIELD_CHARS]


def exception_type_name(exc: BaseException) -> str:
    """`module.Class` for anything outside builtins, plain `Class` for builtins.
    A class name is code, not data -- but it is still clipped, because an
    exception class can be created dynamically with any name."""
    cls = type(exc)
    module = getattr(cls, "__module__", "") or ""
    name = getattr(cls, "__qualname__", cls.__name__)
    return _clip(name if module in ("builtins", "") else "%s.%s" % (module, name))


def relative_frame_path(filename: str, app_root: Optional[Path] = None) -> str:
    """Path relative to the app root, forward slashes; the bare file name for
    third-party packages and for anything outside the root. Never an absolute
    path, never a user-name directory."""
    root = Path(app_root) if app_root is not None else APP_ROOT
    raw = str(filename or "")
    if not raw or raw.startswith("<"):
        return _clip(raw)                      # "<frozen importlib._bootstrap>", "<string>"
    normal = os.path.normcase(os.path.abspath(raw))
    root_normal = os.path.normcase(os.path.abspath(str(root)))
    base = os.path.basename(raw.replace("\\", "/"))
    in_third_party = "site-packages" in normal or "dist-packages" in normal
    if not in_third_party and (normal == root_normal or normal.startswith(root_normal + os.sep)):
        relative = os.path.relpath(os.path.abspath(raw), os.path.abspath(str(root)))
        return _clip(relative.replace("\\", "/"))
    return _clip(base)


def relativize_frames(tb, app_root: Optional[Path] = None) -> list:
    """A traceback -> at most 30 {file, function, line}, INNERMOST first.

    Built with lookup_lines=False and no locals on purpose: the source line
    and the local variables are exactly where customer data lives (a local
    called `customer` holds a customer), so they are never even read.
    """
    if tb is None:
        return []
    summary = traceback.StackSummary.extract(traceback.walk_tb(tb), lookup_lines=False, capture_locals=False)
    frames = [
        {"file": relative_frame_path(f.filename, app_root), "function": _clip(f.name), "line": int(f.lineno or 0)}
        for f in summary
    ]
    frames.reverse()                           # innermost (the crash site) first
    return frames[:MAX_FRAMES]


# A JS stack frame, Chrome ("at fn (url:1:2)" / "at url:1:2") or Firefox/Safari
# ("fn@url:1:2"). The URL is only used to take the script's file name.
_JS_CHROME_RE = re.compile(r"^\s*at\s+(?:(?P<fn>.+?)\s+\()?(?P<url>[^()\s]+?):(?P<line>\d+):(?P<col>\d+)\)?\s*$")
_JS_GECKO_RE = re.compile(r"^\s*(?P<fn>[^@\s]*)@(?P<url>\S+?):(?P<line>\d+):(?P<col>\d+)\s*$")
_JS_ERROR_NAME_RE = re.compile(r"^\s*([A-Za-z_$][\w$]{0,60}(?:Error|Exception))\b")


def frames_from_js_stack(stack: object) -> list:
    """Parse a browser stack string into {file, function, line} frames.

    `file` is the script's BASE NAME only: the URL's host, port, directories,
    query string and fragment are all dropped (a query string can carry
    anything). Lines that are not stack frames are skipped, so a message
    pasted into the stack contributes nothing.
    """
    if not isinstance(stack, str):
        return []
    frames = []
    for line in stack.splitlines():
        match = _JS_CHROME_RE.match(line) or _JS_GECKO_RE.match(line)
        if not match:
            continue
        url = match.group("url")
        try:
            path = urlsplit(url).path or url
        except ValueError:
            path = url
        name = PurePosixPath(path.replace("\\", "/")).name or "<unknown>"
        function = (match.group("fn") or "<anonymous>").strip() or "<anonymous>"
        frames.append({"file": _clip(name), "function": _clip(function), "line": int(match.group("line"))})
        if len(frames) >= MAX_FRAMES:
            break
    return frames


def js_exception_type(stack: object) -> str:
    """`TypeError`, `ReferenceError`... from the first line of a Chrome stack
    ("TypeError: x is not a function"); "Error" when it cannot be told."""
    if isinstance(stack, str):
        match = _JS_ERROR_NAME_RE.match(stack)
        if match:
            return _clip(match.group(1))
    return "Error"


def fingerprint(kind: str, exception_type: str, frames: Iterable[dict]) -> str:
    """sha256(kind | exception_type | first 8 frames as "file:function"), hex[:32].

    Line numbers are deliberately NOT part of it: a rebuild that shifts a
    function down three lines must not split one bug into a new group on
    every release.
    """
    parts = [str(kind), str(exception_type)]
    for frame in list(frames)[:FINGERPRINT_FRAMES]:
        parts.append("%s:%s" % (frame.get("file", ""), frame.get("function", "")))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


def safe_frames(frames: object) -> list:
    """Re-validate frames that came out of storage before they are sent.

    The store only ever holds frames built above, but the sender is the last
    place that can still stop a bad row (an older build's, a hand-edited
    file) from leaving the till, so it coerces every frame into the three
    allowed fields and re-relativizes the path: an absolute path that
    somehow got in is reduced to its bare file name.
    """
    cleaned = []
    if not isinstance(frames, list):
        return cleaned
    for frame in frames[:MAX_FRAMES]:
        if not isinstance(frame, dict):
            continue
        file_value = str(frame.get("file", ""))
        looks_absolute = bool(re.match(r"^[A-Za-z]:[\\/]", file_value)) or file_value.startswith(("/", "\\"))
        if looks_absolute:
            file_value = os.path.basename(file_value.replace("\\", "/"))
        try:
            line = int(frame.get("line", 0))
        except (TypeError, ValueError):
            line = 0
        cleaned.append({"file": _clip(file_value), "function": _clip(frame.get("function", "")), "line": line})
    return cleaned
