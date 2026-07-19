package com.actionaura.clinic.net

import com.actionaura.clinic.ui.i18n.tr
import com.google.gson.Gson
import retrofit2.HttpException
import java.io.IOException
import java.net.SocketTimeoutException

/**
 * Structured API error mapping (Phase 4H / Wave 1A, MOB-003).
 *
 * Retrofit's suspend functions that return a plain body type (not
 * `Response<T>`) throw [HttpException] for any non-2xx response instead of
 * deserializing it -- so a real backend rejection (400 overpayment, 400
 * zero/negative amount, 404 missing invoice, 500 unexpected error) never
 * reached the `r.status == "error"` branch call sites were written for; it
 * fell straight into a generic `catch (e: Exception)` that always showed
 * "Couldn't reach the server" regardless of what actually happened. Found
 * on a real device during Wave 1A physical payment testing.
 *
 * This never displays a raw stack trace or a raw server exception message,
 * and never displays patient data (the classified messages below are
 * static, translated strings -- not the server's literal text).
 */
private data class ErrorBody(val status: String? = null, val message: String? = null)

fun paymentErrorMessage(e: Throwable): String {
    if (e is HttpException) {
        val bodyMsg = try {
            e.response()?.errorBody()?.string()?.let { Gson().fromJson(it, ErrorBody::class.java)?.message }
        } catch (_: Exception) { null } ?: ""

        return when {
            e.code() == 404 ->
                tr("This invoice could not be found. It may have been removed.")
            e.code() == 400 && bodyMsg.contains("greater than zero", ignoreCase = true) ->
                tr("Enter a payment amount greater than zero.")
            e.code() == 400 && bodyMsg.contains("must be a number", ignoreCase = true) ->
                tr("Enter a valid payment amount.")
            e.code() == 400 && bodyMsg.contains("exceeds the outstanding balance", ignoreCase = true) ->
                tr("This amount is more than what's owed on this invoice.")
            e.code() in 500..599 ->
                tr("Something went wrong on the server. Please try again.")
            else ->
                tr("The payment could not be processed.")
        }
    }
    if (e is SocketTimeoutException) return tr("The server took too long to respond. Please try again.")
    if (e is IOException) return tr("Couldn't reach the server. Check your connection and try again.")
    return tr("Something went wrong. Please try again.")
}

/**
 * MOB-004 (Wave 1A, found on a real device): [LoginScreen] had the exact same
 * generic-catch gap as the payment screen above -- commercial_runtime's
 * `/api/auth/login` (see auth_routes.py) rejects bad credentials with a real
 * HTTP 401/403/429, which Retrofit turns into an [HttpException] rather than
 * a 200-with-error-flag body, so the `r.success == false` branch never ran
 * and every rejection showed "Couldn't reach the server" -- including the
 * mundane case of a simply wrong password, discovered when a live device
 * login attempt using another product's admin credentials produced that
 * message even though the server was demonstrably healthy.
 */
fun loginErrorMessage(e: Throwable): String {
    if (e is HttpException) {
        return when (e.code()) {
            401 -> tr("Incorrect email or password.")
            403 -> tr("This account has been disabled. Contact your administrator.")
            429 -> tr("Too many failed attempts. Please wait and try again.")
            400 -> tr("Enter your email and password.")
            in 500..599 -> tr("Something went wrong on the server. Please try again.")
            else -> tr("Couldn't sign in. Please try again.")
        }
    }
    if (e is SocketTimeoutException) return tr("The server took too long to respond. Please try again.")
    if (e is IOException) return tr("Couldn't reach the server. Check your connection and try again.")
    return tr("Something went wrong. Please try again.")
}

/** Same generic-catch gap as [loginErrorMessage]/[paymentErrorMessage], for booking. */
fun appointmentErrorMessage(e: Throwable): String {
    if (e is HttpException) {
        return when (e.code()) {
            404 -> tr("That patient or doctor could not be found.")
            409 -> tr("This doctor already has an appointment at that time.")
            400 -> tr("Pick a patient and a valid date and time.")
            in 500..599 -> tr("Something went wrong on the server. Please try again.")
            else -> tr("Couldn't book the appointment. Please try again.")
        }
    }
    if (e is SocketTimeoutException) return tr("The server took too long to respond. Please try again.")
    if (e is IOException) return tr("Couldn't reach the server. Check your connection and try again.")
    return tr("Something went wrong. Please try again.")
}
