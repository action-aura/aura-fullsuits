# Inviting staff crashed on every device whose staff list had synced — root cause

Found 2026-09-06 17:52, the evening before the first customer demo, fixed
and re-measured the same hour. Written in the same shape as
`employee-locked-out-of-retail-root-cause-analysis.md` because it is the
same family of defect: a single-device assumption that survived every test
and died on the second device.

## What the owner would have seen

Employees → Invite on the demo laptop: **"Internal Server Error"**. The same
on the phone. Step 1 of `sunday-demo-runbook.md` — "create staff accounts on
the spot" — would have failed on stage, on both devices.

## Measured

- Phone, freshly joined through the new first-run door
  (`scripts/ops/phone_offline_and_staff.py`, 17:50):
  `POST /api/admin/employees` → **500**. logcat:

      File ".../commercial_runtime/identity/onboarding_routes.py", line 1022, in create_employee
      sqlite3.IntegrityError: UNIQUE constraint failed: users.company_id, users.employee_id

- The demo laptop's own till (`:5010`, 17:52), same request → **500**.
  Its staff list: `ADMIN-0001, ADMIN-0002, EMP-0002, EMP-0003, EMP-0006`.
- A joined desktop till (`:5013`) → **500**, same list.

## Root cause

`create_employee` minted the next code as

    count = SELECT COUNT(*) FROM users WHERE company_id=?
    emp_id = f"EMP-{count + 1:04d}"

which assumes every code in the table was minted **here**, one after
another. True on one device forever. False the moment one row arrives by
sync: the five accounts above are two owners and three cashiers from three
devices, numbered `…-0002, -0003, -0006` — five rows, so `count + 1` is
**6**, and `EMP-0006` already exists. The insert hits
`UNIQUE(company_id, employee_id)` and the route has no handler for it.

It did not fail earlier in the two nights of hardware proofs only because
the numbers happened not to line up: the phone's own invite at 17:13 (before
the wipe) counted four local rows and minted `EMP-0006` into a table that
did not yet hold one. The re-numbering the sync apply gained on 2026-09-05
(`SyncService._resolve_employee_code`) protects **arrival**; nothing
protected **minting**.

## The fix

`_next_employee_number(conn, company_id)` returns
`max(COUNT(users), highest existing EMP suffix) + 1`.

- `max(count, highest)` rather than `highest` alone: on a fresh install the
  only row is `ADMIN-0001`, whose suffix is not an EMP suffix, and the first
  invite has always been `EMP-0002` (= count + 1). Keeping the count in the
  max makes the answer byte-for-byte the old one wherever the old one was
  not a collision — no existing single-device install changes numbering,
  which is what design §2.4 D0 ("the admin arm's format is untouched")
  actually needs.
- The delegated arm (`EMP-<dev4>-NNNN`) uses the same number; the trailing
  digits are what is parsed, so both arms count.
- Non-numeric tails (the sync apply's uid-suffixed re-numbering) are skipped.
- Two devices minting the same number offline remains possible and remains
  the sync apply's job on arrival — the two halves of one rule.

## Proof

- `products/retail/tests/retail_employee_code_after_sync_test.py`: red on
  the old allocator with exactly the phone's shape (synced `EMP-0004` sitting
  at `count + 1` → `IntegrityError`), green after; a fresh install's first
  invite pinned as `EMP-0002`.
- Demo laptop till after the restart: the same request → **200** with a
  setup link.
- Phone, fixed build installed over its data: cashier created **on the
  phone** → password set through its setup link → visible on the laptop as
  `active` in 15 s → signed in **on the laptop** as `EMP-0008`. Then the
  offline sale: rung with the relay cut, invisible to the laptop while
  offline, on the laptop 5 s after reconnect, stock 9 on both.

## What was left

The one-process run of `commercial_runtime/identity/tests` shows ten
failures of the "An admin account already exists" shape, all in
`test_registry_v4_session_invalidation.py` (6) and
`test_registry_v4_window.py` (4). Both files pass alone (6 and 6 — checked
2026-09-06 18:40). That is the cross-file fixture pollution the canonical
runner exists to avoid (one pytest process per file; see
`products/run_all_tests.py`'s docstring) and it predates this change, which
touches only the employee-code allocator; noted here so the next person who
runs the directory in one process does not chase it as a regression.
