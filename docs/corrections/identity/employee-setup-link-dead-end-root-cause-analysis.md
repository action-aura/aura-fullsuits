# The invite link led to a sign-in screen the new employee could not pass — root cause

Found 2026-09-06 20:15, the evening before the first customer demo, by
walking the demo's own steps through the real screens rather than the API.

## What the owner would have seen

Employees → "+ Add Employee" → email, Cashier, **Create Invite** → the dialog
says "Send this link to cashier.demo@rehearsal.local" and shows
`http://127.0.0.1:5010/#setup/f94cf30d…` with "This link works once and
expires in 7 days." Opening that link, on the laptop or anywhere: **the
ordinary sign-in screen** — "Welcome to Aura Retail / Sign in to your store",
email and password. The invited person has no password. There is no field
to set one. Step 2 of `sunday-demo-runbook.md` ("Open the setup link, set a
password, 20 seconds") would have stalled on stage with the audience
watching.

## Why it was green anyway

The backend half was complete: `POST /api/auth/employee/setup {token,
password}` (`onboarding_routes.py::employee_setup`) validates the token,
enforces the six-character minimum, activates the account and queues the
sync event. Every proof of the staff flow — `phone_offline_and_staff.py`,
the employee tests, the runbook's own rehearsal — set the password by
calling that route directly. `SubsystemApp.init()` handles two boot-time
fragments, `#verify-email/<token>` and `#reset-password/<token>`, and
`onboarding_routes.py`'s own comment describes the invite link as
"currently frontend-unwired". The dialog issued a link nothing rendered.
The runbook's step 2 was written from the API run and did not say so.

## The fix

`#setup/<token>` joins the two handled fragments in `init()` and opens
`_showEmployeeSetupScreen(token)`: password and confirmation, the same
validation the reset-password screen applies, one `POST
/api/auth/employee/setup`, then "Your password is set — sign in with your
email and this password, on this device or the phone" and a button to the
sign-in screen. It mirrors `_showResetPasswordScreen` /
`_resetPasswordSubmit` deliberately, so the two self-service password
screens cannot drift apart. English and Arabic catalog entries added.

## Proof

- `products/retail/tests/retail_employee_setup_link_test.js` drives
  `init()` with the fragment set, checks the page renders before any
  onboarding call, that submit posts exactly `{token, password}` to the
  employee-setup route, that short or mismatched passwords never post, that
  a server refusal is shown verbatim, and that a mutant posting to the
  reset-password route goes red.
- Real run, 2026-09-06 20:40: the invite for `cashier.demo@rehearsal.local`
  created through the dialog at 20:15 (its link had shown the sign-in
  screen), opened again in a fresh Chromium after the fix → "Set your
  password" → password and confirmation → "Your password is set. Sign in
  with your email and this password, on this device or the phone." → on the
  Mi Note 10, that email and password typed into the real sign-in form →
  the till's dashboard. The link is single-use: the first, failed opening
  had not consumed it, because no page ever posted the token.

## The lesson, for the runbook and the next reader

A step that was proven only through the API must say so, or it reads as a
proven screen. The runbook's step 2 now names the page and the date it was
first walked for real.
