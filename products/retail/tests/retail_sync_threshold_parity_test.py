"""Aura Retail -- the staleness threshold exists twice, in two languages, and
this file is what stops the two copies drifting apart.

Launch-readiness Phase 7. One conceptual value -- "how far behind is too far
behind to trust a locally-scoped fact" -- is read on both sides of a boundary
that has no shared runtime:

  * Python: `commercial_runtime/sync/sync_service.py`'s
    `SYNC_STALE_THRESHOLD_SECONDS`, which stage 7c-i's
    `_is_device_behind_on_sync()` uses to refuse a purchase-order receipt.
  * JavaScript: `products/retail/frontend/subsystem-retail.js`'s
    `RetailSystem.SYNC_STALE_THRESHOLD_SECONDS`, which stage 7b uses to decide
    when the POS tile stops stating a stock figure and starts dating it.

This codebase is a Flask backend and a deliberately build-step-free vanilla-JS
frontend (see CLAUDE.md: no bundler, no framework, no transpilation). There is
no module either language can import, so ONE literal cannot serve both. Both
sites carry a comment saying "change both together" -- but a comment is not a
mechanism, and two numbers that must agree, in two files, in two languages,
maintained by two different kinds of edit, is exactly the shape that drifts
silently and is discovered by a shop instead of by a test.

What drift would actually cost, which is why this is worth a file of its own:
the two halves would disagree about whether the device is behind. The till
would still present a stock number as current while the backend had already
decided the device was too stale to trust its own PO-receipt guard -- or, worse
in the other direction, the backend would refuse a delivery the operator has no
on-screen reason to believe is refusable, because the tile still looks fresh.
Neither failure raises anything; both just behave oddly.

Deliberately NOT solved by having the backend serve the number to the frontend
in `/sync/health`. That is a real option and a defensible one, but it makes the
POS tile's rendering depend on a network payload having a field, for a value
that has never changed, and this test costs a great deal less. If the threshold
ever becomes operator-configurable, revisit that -- at which point the backend
becomes the single source and this file is deleted rather than updated.

Run:
    pytest products/retail/tests/retail_sync_threshold_parity_test.py -v
"""
import re
from pathlib import Path

from commercial_runtime.sync.sync_service import SYNC_STALE_THRESHOLD_SECONDS

FRONTEND_JS = (
    Path(__file__).resolve().parents[1] / "frontend" / "subsystem-retail.js"
)

#: `30 * 60`, `1800`, `60*30` -- an arithmetic expression of integers only.
#: Restricted to digits, `*` and whitespace ON PURPOSE: this is the whole
#: input we will evaluate, so the grammar it accepts is the security boundary,
#: not a convenience. A JS edit introducing anything else (a variable, a
#: function call, a different operator) fails the match loudly rather than
#: being silently evaluated.
_JS_CONSTANT = re.compile(
    r"SYNC_STALE_THRESHOLD_SECONDS\s*:\s*([0-9][0-9\s*]*?)\s*,", re.MULTILINE
)


def _js_threshold_seconds() -> int:
    """Reads the frontend's own constant out of the real shipped file.

    Reads the FILE rather than a copy of the number, which is the entire point
    -- a test asserting `1800 == 1800` against two literals it declares itself
    would pass forever while the real files diverged.
    """
    source = FRONTEND_JS.read_text(encoding="utf-8")
    match = _JS_CONSTANT.search(source)
    assert match, (
        "could not find RetailSystem.SYNC_STALE_THRESHOLD_SECONDS in "
        f"{FRONTEND_JS.name}. If it was renamed or moved, this test must be "
        "updated deliberately -- do not delete it, the two values still have "
        "to agree."
    )
    expression = match.group(1)
    assert re.fullmatch(r"[0-9][0-9\s*]*", expression), (
        f"unexpected expression {expression!r} -- see _JS_CONSTANT's comment"
    )
    # Safe by construction: the assertion above admits only digits, `*` and
    # whitespace, so there is nothing here to evaluate but multiplication.
    return int(eval(expression))  # noqa: S307 - grammar restricted above


def test_the_frontend_and_backend_staleness_thresholds_are_the_same_number():
    assert _js_threshold_seconds() == SYNC_STALE_THRESHOLD_SECONDS, (
        "the POS tile and the PO-receipt guard would disagree about whether "
        "this device is behind: frontend "
        f"{_js_threshold_seconds()}s vs backend {SYNC_STALE_THRESHOLD_SECONDS}s. "
        "Change both, or neither."
    )


def test_the_frontend_constant_is_actually_present_and_parsed():
    """Guards the guard.

    If `_js_threshold_seconds()` ever silently returned a default instead of
    failing -- a refactor, a renamed constant, a regex that stopped matching --
    the parity test above would compare that default against the backend and
    could pass while checking nothing at all. So this asserts the value was
    genuinely read from the file and is a plausible duration, independently of
    what the backend happens to hold.
    """
    seconds = _js_threshold_seconds()
    assert isinstance(seconds, int) and seconds > 0
    assert 60 <= seconds <= 24 * 60 * 60, (
        f"{seconds}s is not a plausible staleness threshold; if the design "
        "genuinely changed, update this bound deliberately"
    )
