package com.actionaura.clinic.ui

import com.actionaura.clinic.net.User
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Phase 4K role-derivation contract tests. Mirrors
 * docs/security/clinic-rbac-matrix.md's real, verified binary model
 * exactly: admin bypasses everything; clinic_role=="doctor" is the only
 * other distinguished role; anything else (blank, unrecognized, or
 * missing) is secretary -- never Admin as a fallback.
 */
class ClinicSessionTest {

    @Test
    fun admin_role_wins_regardless_of_clinic_role() {
        val u = User(role = "admin", clinic_role = "doctor")
        assertEquals(ClinicRole.ADMIN, deriveClinicRole(u))
    }

    @Test
    fun doctor_clinic_role_maps_to_doctor() {
        val u = User(role = "employee", clinic_role = "doctor")
        assertEquals(ClinicRole.DOCTOR, deriveClinicRole(u))
    }

    @Test
    fun blank_clinic_role_maps_to_secretary_the_matrixs_default_staff() {
        val u = User(role = "employee", clinic_role = "")
        assertEquals(ClinicRole.SECRETARY, deriveClinicRole(u))
    }

    @Test
    fun explicit_secretary_clinic_role_maps_to_secretary() {
        val u = User(role = "employee", clinic_role = "secretary")
        assertEquals(ClinicRole.SECRETARY, deriveClinicRole(u))
    }

    @Test
    fun null_or_unrecognized_role_never_defaults_to_admin() {
        assertEquals(ClinicRole.SECRETARY, deriveClinicRole(null))
        assertEquals(ClinicRole.SECRETARY, deriveClinicRole(User(role = null, clinic_role = null)))
        assertEquals(ClinicRole.SECRETARY, deriveClinicRole(User(role = "something-unexpected", clinic_role = "also-unexpected")))
    }

    @Test
    fun clinicSession_reset_returns_to_secretary() {
        ClinicSession.update(User(role = "admin"))
        assertEquals(ClinicRole.ADMIN, ClinicSession.role)
        ClinicSession.reset()
        assertEquals(ClinicRole.SECRETARY, ClinicSession.role)
    }

    @Test
    fun canDoDoctorOnlyActions_true_for_doctor_and_admin_false_for_secretary() {
        ClinicSession.update(User(role = "employee", clinic_role = "doctor"))
        assert(ClinicSession.canDoDoctorOnlyActions)
        ClinicSession.update(User(role = "admin"))
        assert(ClinicSession.canDoDoctorOnlyActions)
        ClinicSession.update(User(role = "employee", clinic_role = ""))
        assert(!ClinicSession.canDoDoctorOnlyActions)
        ClinicSession.reset()
    }
}
