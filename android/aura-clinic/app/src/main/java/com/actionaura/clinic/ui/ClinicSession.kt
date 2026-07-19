package com.actionaura.clinic.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.actionaura.clinic.net.User

/**
 * Client-side role state for UI-level gating (Phase 4K / Wave 1A, Part K).
 *
 * This is usability/exposure-reduction only -- the backend route
 * authorization in products/clinic/backend/api/clinic_api.py
 * (`require_clinic_role`) remains the actual security boundary and is
 * unchanged by anything here. See docs/security/clinic-rbac-matrix.md for
 * the real, verified enforcement model this mirrors.
 *
 * Deliberately minimal: the RBAC matrix's only two gated things are (1)
 * admin-only user management (the onboarding_routes.py admin endpoints,
 * which have no Android screen at all -- the mobile app has no
 * employee-management UI in source to gate) and (2) five doctor-only
 * WRITE actions (update visit diagnosis/
 * treatment, add clinical note, add/delete follow-up, create prescription
 * -- none of which have any Android UI either; PrescriptionsScreen.kt,
 * for example, is read-only/list-only in source, with no create form).
 * Everything else in the matrix is "any authenticated clinic staff," which
 * is already the app's default (no additional gating needed). This object
 * exists so that role state is derived once, correctly, and is ready to
 * gate a doctor-only action the moment one is ever added to the mobile UI
 * -- not to retrofit gates onto actions that don't exist yet, which would
 * misrepresent what this migration phase actually changed.
 */
enum class ClinicRole { ADMIN, DOCTOR, SECRETARY }

/** Pure derivation, matching docs/security/clinic-rbac-matrix.md's binary
 * model exactly: admin bypasses everything; clinic_role=="doctor" is the
 * only other distinguished role; anything else (including a blank
 * clinic_role) is "secretary" (default staff, per the matrix's own
 * wording). Never assume Admin as a fallback when role parsing is
 * ambiguous -- unrecognized/missing role data defaults to the most
 * restrictive real role (secretary), not the most permissive. */
fun deriveClinicRole(user: User?): ClinicRole = when {
    user == null -> ClinicRole.SECRETARY
    user.role == "admin" -> ClinicRole.ADMIN
    user.clinic_role == "doctor" -> ClinicRole.DOCTOR
    else -> ClinicRole.SECRETARY
}

object ClinicSession {
    var role by mutableStateOf(ClinicRole.SECRETARY)
        private set

    fun update(user: User?) {
        role = deriveClinicRole(user)
    }

    fun reset() {
        role = ClinicRole.SECRETARY
    }

    val isAdmin: Boolean get() = role == ClinicRole.ADMIN
    val isDoctor: Boolean get() = role == ClinicRole.DOCTOR
    val canDoDoctorOnlyActions: Boolean get() = role == ClinicRole.DOCTOR || role == ClinicRole.ADMIN
}
