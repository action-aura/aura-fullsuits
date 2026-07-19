# Clinic — Core Workflow: Patient / Appointment / Invoice / Payment (Wave 1A, Part I)

## Patient
Created **Test Patient One**, phone `0000000000` (no real PII). Confirmed persisted via `GET /api/sub/clinic/patients`: `id: 1`, `patient_code: P-1784327470-579`.

## Appointment — MOB-005 found and fixed
First booking attempt appeared to save but never showed on the Appointments screen. Root-caused: free-text date/time entry with no validation let a malformed string (`2026-7-18`, no zero-padding, no time) through to the backend, which stored it successfully but could never match it against any `date(appointment_dt)=?` schedule query again — the appointment was silently orphaned. Fixed by replacing free text with native `DatePicker`/`TimePicker` dialogs, and by extending the Appointments screen to an unbounded "upcoming from today" view (backend `from_date` range query) instead of a single-day-only filter, since a legitimately future-dated appointment had the identical "invisible" symptom for a different reason. Full detail in the defect registry (MOB-005).

Verified post-fix: appointments for both today (`2026-07-18 09:00`) and a future date (`2026-07-19 13:00`) both correctly listed once the fix was live.

## Dashboard "Scheduled today" clarification
User observed 4 total appointments booked but the dashboard showing "1". Investigated and confirmed correct, not a bug: only 1 of the 4 was actually dated today — the stat is intentionally today-only, matching its label.

## Invoice
Created a $100.00 invoice for Test Patient One. Confirmed via `GET /api/sub/clinic/invoices`: `status: paid`, `total: 100.0`, `amount_paid: 100.0` after payment.

## Payment — MOB-003 verified live
Recording a payment above the outstanding balance ($150 against $100 owed) correctly showed "This amount is more than what's owed on this invoice." instead of the pre-fix "Couldn't reach the server." Recording the correct $100.00 amount closed the invoice.

## Invoice drill-down (gap found and fixed, same investigation)
Invoice cards showed a summary only — no way to see line items or payment history, even though the backend's `GET /invoices/{id}` already returns both. Added a tap-to-open detail sheet (`InvoiceDetailSheet` in `BillingScreen.kt`) showing items, total/paid/balance-due, and full payment history, with a "Record Payment" action for unpaid invoices.

## Result
PASS after MOB-003/005 fixes and the invoice drill-down addition. Full patient → appointment → invoice → payment lifecycle exercised end-to-end through the real UI, cross-checked against the live backend at every step.
