# Clinic — Payment Error Handling (Wave 1A, Part J)

## The documented gap this wave was required to fix
Retrofit suspend functions returning a plain body type (not `Response<T>`) throw `HttpException` for any non-2xx response instead of deserializing it. Every real backend rejection (400 overpayment, 400 zero/negative amount, 404 missing invoice) therefore fell into a generic `catch (e: Exception)` that always showed "Couldn't reach the server," regardless of the real cause.

## Fix
`paymentErrorMessage(e: Throwable): String` in `android/aura-clinic/app/src/main/java/com/actionaura/clinic/net/ApiErrors.kt`, parsing the real error body (`{status, message}`) and classifying:
- 404 → "This invoice could not be found. It may have been removed."
- 400 + "greater than zero" → "Enter a payment amount greater than zero."
- 400 + "must be a number" → "Enter a valid payment amount."
- 400 + "exceeds the outstanding balance" → "This amount is more than what's owed on this invoice."
- 5xx → "Something went wrong on the server. Please try again."
- unrecognized 400 → "The payment could not be processed."
- `SocketTimeoutException` → timeout-specific message
- `IOException` → connectivity message
- anything else → generic safe message

Never displays raw exception text, stack traces, or patient data. All messages translated (English/Arabic) via `tr()`.

## Note on "cancelled invoice"
Per Wave 0's own findings, Clinic invoices do not support a `cancelled` status at all — there is no such state to handle, so no branch for it was added (would have been a fabricated case).

## Tests
10 unit tests in `ApiErrorsTest.kt`, constructing real `HttpException` fixtures via `retrofit2.Response.error()` + an OkHttp `ResponseBody`, covering every branch above plus "malformed error body doesn't crash the mapper" and "unexpected exception never shows a raw message."

## Device verification
Live on-device: $150 payment against a $100 invoice → "This amount is more than what's owed on this invoice." (not the old generic message).

## Related: same gap found in Login (MOB-004) and Appointment booking (part of MOB-005)
The identical systemic pattern was independently discovered in two other screens during this wave and fixed the same way — see `loginErrorMessage()` and `appointmentErrorMessage()` in the same file.

## Result
PASS.
