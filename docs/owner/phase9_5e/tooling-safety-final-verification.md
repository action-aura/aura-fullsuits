# Phase 9.5E Milestone 26 — Tooling Safety, Final Verification

`docs/owner/phase9_5e/tooling-safety-audit.md` (Milestone 0) audited the
`.autosync` tooling and this phase's own dev-server harness *before* any
Phase 9.5E code existed. This document is the end-of-phase counterpart: a
real static-analysis scan (`bandit -r`) of every application directory this
phase actually wrote, run on 2026-08-03 against the finished code, not a
carry-forward of the M0 reasoning.

## Scope scanned

```
app/expenses  app/cash_closing  app/operational_reports  app/management_notes
app/operations_ui  app/api_operations/expenses_and_operations.py
tools/dev_server
```

## Result

```
_totals: SEVERITY.HIGH 0, SEVERITY.MEDIUM 1, SEVERITY.LOW 8  (9 findings, all CONFIDENCE.HIGH)
```

**Zero findings in any application code directory** (`app/expenses`,
`app/cash_closing`, `app/operational_reports`, `app/management_notes`,
`app/operations_ui`, `app/api_operations/expenses_and_operations.py` all
report 0/0/0/0). **All 9 findings are in `tools/dev_server/port_isolation.py`**
-- the M0 dev-only browser-test harness, never imported by, deployed with,
or reachable from the production application.

| Line(s) | Test ID | Severity | Finding | Why it's a false positive here |
|---|---|---|---|---|
| 23 | B404 | LOW | `subprocess` module import flagged | Blanket blacklist flag on the import itself; the actual calls are reviewed individually below |
| 71, 165, 182 | B607 | LOW (x3) | "partial executable path" (`powershell.exe`, `taskkill`) | Resolved via the OS `PATH`, the standard way to invoke Windows system tools; not attacker-influenced |
| 71, 112, 165, 182 | B603 | LOW (x4) | "subprocess call - check for execution of untrusted input" | Every argument list is a fixed literal or an internally-generated `int` (`pid`, `port`) -- never shell-interpreted (`shell=True` is never used), never derived from HTTP request input or any external/network source |
| 128 | B310 | MEDIUM | `urllib.request.urlopen()` on a non-allowlisted scheme | The URL is always `http://127.0.0.1:{port}{health_path}` -- a locally-constructed string using the harness's own just-picked ephemeral port, never a caller- or network-supplied URL |

Raw JSON preserved at `scratchpad/bandit_9_5e_final.json`.

## Cross-check against M0's own tooling audit

M0's `tooling-safety-audit.md` reasoned from first principles (before this
code existed) that a dev-only, ephemeral-port, own-PID-only process harness
was the right shape to avoid the `.autosync` blast-radius class. This
scan is the empirical confirmation of that design after the fact: every one
of the 9 findings is exactly the class of finding a subprocess-based local
dev tool is expected to trigger, none of them touch production code paths,
and — as `infrastructure-security-regression.md` §4 shows — the harness was
actually exercised (real dev-server start/stop, real HTTP health checks)
during this same milestone without incident.

## Disposition

**No fixes required.** All 9 findings are accepted, reviewed, false-positive
dev-tooling findings, consistent with the M0 design intent and with zero
findings anywhere in the 6 real application-code directories this phase
delivered.
