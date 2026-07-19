package com.actionaura.clinic.net

import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.io.IOException
import java.net.SocketTimeoutException

/**
 * Phase 4H / Wave 1A (MOB-003) error-mapping contract tests. Proves
 * paymentErrorMessage() classifies each real backend rejection shape
 * (reproduced physically on-device, see docs/mobile/wave1a/clinic-payment-error-handling.md)
 * into a distinct, non-generic message -- not the pre-fix behavior of
 * always showing "Couldn't reach the server" for every HttpException.
 */
class ApiErrorsTest {

    private fun httpError(code: Int, json: String): HttpException {
        val body = json.toResponseBody("application/json".toMediaTypeOrNull())
        return HttpException(Response.error<Any>(code, body))
    }

    @Test
    fun zero_or_negative_amount_maps_to_a_specific_message() {
        val msg = paymentErrorMessage(httpError(400, """{"status":"error","message":"Payment amount must be greater than zero."}"""))
        assertEquals("Enter a payment amount greater than zero.", msg)
    }

    @Test
    fun malformed_amount_maps_to_a_specific_message() {
        val msg = paymentErrorMessage(httpError(400, """{"status":"error","message":"Payment amount must be a number."}"""))
        assertEquals("Enter a valid payment amount.", msg)
    }

    @Test
    fun overpayment_maps_to_a_specific_message() {
        val msg = paymentErrorMessage(httpError(400, """{"status":"error","message":"Payment of 70.00 exceeds the outstanding balance of 60.00. This product has no customer-credit ledger, so overpayment cannot be accepted."}"""))
        assertEquals("This amount is more than what's owed on this invoice.", msg)
    }

    @Test
    fun missing_invoice_maps_to_a_specific_message() {
        val msg = paymentErrorMessage(httpError(404, """{"status":"error","message":"Invoice not found"}"""))
        assertEquals("This invoice could not be found. It may have been removed.", msg)
    }

    @Test
    fun server_error_maps_to_a_generic_but_distinct_message() {
        val msg = paymentErrorMessage(httpError(500, """{"status":"error","message":"Could not record payment."}"""))
        assertEquals("Something went wrong on the server. Please try again.", msg)
    }

    @Test
    fun unrecognized_400_falls_back_to_a_generic_payment_message() {
        val msg = paymentErrorMessage(httpError(400, """{"status":"error","message":"Some new validation rule."}"""))
        assertEquals("The payment could not be processed.", msg)
    }

    @Test
    fun malformed_error_body_does_not_crash_the_mapper() {
        // Not valid JSON -- must not throw, must still return a safe message.
        val msg = paymentErrorMessage(httpError(400, "not json at all"))
        assertEquals("The payment could not be processed.", msg)
    }

    @Test
    fun timeout_maps_to_a_timeout_specific_message() {
        val msg = paymentErrorMessage(SocketTimeoutException("timeout"))
        assertEquals("The server took too long to respond. Please try again.", msg)
    }

    @Test
    fun generic_network_failure_maps_to_a_connectivity_message() {
        val msg = paymentErrorMessage(IOException("connection refused"))
        assertEquals("Couldn't reach the server. Check your connection and try again.", msg)
    }

    @Test
    fun unexpected_exception_never_shows_a_raw_message_or_stack_trace() {
        val msg = paymentErrorMessage(RuntimeException("NullPointerException at com.actionaura.clinic.Foo.bar(Foo.kt:42)"))
        assertEquals("Something went wrong. Please try again.", msg)
    }

    // ── loginErrorMessage() -- MOB-004, found live on-device: a plain wrong
    // password produced "Couldn't reach the server" because /api/auth/login's
    // real 401/403/429/400 responses (see commercial_runtime/identity/auth_routes.py)
    // throw HttpException, same systemic gap as the payment mapper above. ──

    @Test
    fun wrong_credentials_maps_to_a_specific_message_not_a_network_message() {
        val msg = loginErrorMessage(httpError(401, """{"error":"Invalid email or password."}"""))
        assertEquals("Incorrect email or password.", msg)
    }

    @Test
    fun disabled_account_maps_to_a_specific_message() {
        val msg = loginErrorMessage(httpError(403, """{"error":"This account has been disabled."}"""))
        assertEquals("This account has been disabled. Contact your administrator.", msg)
    }

    @Test
    fun locked_account_maps_to_a_specific_message() {
        val msg = loginErrorMessage(httpError(429, """{"error":"Too many attempts."}"""))
        assertEquals("Too many failed attempts. Please wait and try again.", msg)
    }

    @Test
    fun missing_credentials_maps_to_a_specific_message() {
        val msg = loginErrorMessage(httpError(400, """{"error":"Email and password are required"}"""))
        assertEquals("Enter your email and password.", msg)
    }

    @Test
    fun login_server_error_maps_to_a_generic_but_distinct_message() {
        val msg = loginErrorMessage(httpError(500, """{"error":"boom"}"""))
        assertEquals("Something went wrong on the server. Please try again.", msg)
    }

    @Test
    fun login_network_failure_maps_to_a_connectivity_message() {
        val msg = loginErrorMessage(IOException("connection refused"))
        assertEquals("Couldn't reach the server. Check your connection and try again.", msg)
    }

    @Test
    fun login_unexpected_exception_never_shows_a_raw_message() {
        val msg = loginErrorMessage(RuntimeException("boom"))
        assertEquals("Something went wrong. Please try again.", msg)
    }

    // ── appointmentErrorMessage() -- same systemic HttpException gap, booking flow. ──

    @Test
    fun appointment_patient_or_doctor_not_found_maps_to_a_specific_message() {
        val msg = appointmentErrorMessage(httpError(404, """{"status":"error","message":"Patient not found"}"""))
        assertEquals("That patient or doctor could not be found.", msg)
    }

    @Test
    fun appointment_double_booking_maps_to_a_specific_message() {
        val msg = appointmentErrorMessage(httpError(409, """{"status":"error","message":"Doctor already has an appointment at this time"}"""))
        assertEquals("This doctor already has an appointment at that time.", msg)
    }

    @Test
    fun appointment_missing_patient_id_maps_to_a_specific_message() {
        val msg = appointmentErrorMessage(httpError(400, """{"status":"error","message":"patient_id is required"}"""))
        assertEquals("Pick a patient and a valid date and time.", msg)
    }

    @Test
    fun appointment_network_failure_maps_to_a_connectivity_message() {
        val msg = appointmentErrorMessage(IOException("connection refused"))
        assertEquals("Couldn't reach the server. Check your connection and try again.", msg)
    }
}
