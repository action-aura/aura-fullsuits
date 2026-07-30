# Phase 8V-P4 — Logcat Privacy Review — **PASS (scope actually reviewed)**

## Method

`adb logcat -d --pid=<app pid>` filtered to each product's own process (not full-device noise),
searched for both license key strings (`AURA-CLN-...`, `AURA-RET-...`) after each real
activation/check-in/renewal/conversion this session.

## Result

```
$ adb logcat -d --pid=<clinic pid> | grep -i "AURA-CLN"
(no output)
$ adb logcat -d --pid=<retail pid> | grep -i "AURA-RET"
(no output)
```

Zero matches across all scenarios exercised this session (initial activation x2, early renewal,
late renewal, pilot conversion, emergency extension, multiple check-ins, multiple force-stop/
relaunch cycles). No exceptions or stack traces appeared in either app's own process log during any
of these real operations (`grep -iE "except|error|fatal|crash"` also returned nothing for either
app's own PID, across the whole session).

## Diagnostic prints (this session's own, since reverted)

While investigating the `OWNER_LICENSING_BASE_URL` false alarm (see `phase8vp4-baseline.md`), two
temporary `print()` diagnostics were added to `main.py`/`config.py` and appeared in Logcat under
`python.stdout` -- both printed only the *configured URL*, never a license key, device key, or any
customer data, and both were reverted before the final rebuild (confirmed via `git status`/`git
diff` showing a clean tree before commit -- see `phase8-final-unconditional-decision.md`).

## Not independently exercised this session

The full scenario list this phase's own brief names (past due, restricted mode, device replacement,
plan downgrade, invalid assertion, network failure) was not each individually captured and searched
-- the checks above cover every scenario this session actually ran physically, not the full
hypothetical list.

## Result: **PASS** for the scope actually reviewed -- zero secret leakage, zero customer-domain
data, zero raw exceptions observed in either product's own process log.
