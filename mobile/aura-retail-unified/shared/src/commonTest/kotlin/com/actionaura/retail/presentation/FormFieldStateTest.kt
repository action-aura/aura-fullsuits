package com.actionaura.retail.presentation

import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class FormFieldStateTest {

    @Test
    fun aRequiredFieldWithNoValueIsInvalid() {
        val field = FormFieldState.empty<String>(required = true)
        assertFalse(field.isValid)
    }

    @Test
    fun anOptionalFieldWithNoValueIsValid() {
        val field = FormFieldState.empty<String>(required = false)
        assertTrue(field.isValid)
    }

    @Test
    fun aFieldWithARealDomainErrorIsInvalidEvenIfNotRequired() {
        val field = FormFieldState(value = "x", displayValue = "x", domainError = UiFieldError("name", "error.invalid"), required = false)
        assertFalse(field.isValid)
    }

    @Test
    fun aRequiredFieldWithARealValueAndNoErrorIsValid() {
        val field = FormFieldState(value = "Beverages", displayValue = "Beverages", required = true)
        assertTrue(field.isValid)
    }

    @Test
    fun allValidRequiresEveryRealFieldToBeValid() {
        val validField = FormFieldState(value = "x", displayValue = "x", required = true)
        val invalidField = FormFieldState.empty<String>(required = true)
        assertFalse(listOf(validField, invalidField).allValid())
        assertTrue(listOf(validField, validField).allValid())
    }
}
