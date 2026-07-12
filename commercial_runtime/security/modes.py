"""
Aura FullSuits -- runtime mode boundary.

Single source of truth for "which environment is this process running in."
Every dev-only / demo-only code path must gate on the functions below instead
of checking `os.environ.get('AURA_DEV')` (etc.) directly, so the fail-closed
rule lives in exactly one place.

Modes
-----
PRODUCTION STANDALONE
    A frozen PyInstaller Windows exe, or an Android build launched with
    AURA_STANDALONE=1 (both mean: `sys.frozen` is True, OR the platform layer
    explicitly declared itself standalone). This is what ships to a paying
    customer. `dev_mode_enabled()` and `demo_mode_enabled()` are HARD OFF
    here -- IS_FROZEN short-circuits both functions to False before the
    environment variable is even read, so a customer setting an environment
    variable on their machine cannot unlock either mode in a real build.

DEVELOPMENT
    Running from source with AURA_DEV=1 explicitly exported first. Not
    reachable in a frozen build.

DEMO PORTAL / DEMO RESET
    Running from source with AURA_RETAIL_DEMO_MODE=1 (or the per-product
    equivalent) explicitly exported. Never reachable in a frozen build
    regardless of environment variables.

Extracted verbatim from Action Aura Enterprise's core/security/modes.py.
"""
import os
import sys

IS_FROZEN: bool = bool(getattr(sys, "frozen", False))


def dev_mode_enabled() -> bool:
    """True only for a non-frozen process with AURA_DEV=1 explicitly set."""
    return (not IS_FROZEN) and os.environ.get("AURA_DEV") == "1"


def retail_demo_mode_enabled() -> bool:
    """Gates the retail demo-seed/demo-wipe routes. True only for a
    non-frozen process with AURA_RETAIL_DEMO_MODE=1 explicitly set. Always
    False in a shipped exe/APK, no matter the environment."""
    return (not IS_FROZEN) and os.environ.get("AURA_RETAIL_DEMO_MODE") == "1"
