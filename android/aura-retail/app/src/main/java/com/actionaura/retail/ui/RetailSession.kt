package com.actionaura.retail.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.actionaura.retail.net.User

/**
 * Client-side admin-state for UI-level gating (Wave 1A, Part G). Usability
 * only -- the backend independently enforces admin-only access on the
 * backup/restore endpoints (commercial_runtime/backup/routes.py) regardless
 * of what this UI shows. Never assume Admin as a fallback when role data
 * is missing or unrecognized.
 */
fun isAdminUser(user: User?): Boolean = user?.role == "admin"

object RetailSession {
    var isAdmin by mutableStateOf(false)
        private set

    fun update(user: User?) {
        isAdmin = isAdminUser(user)
    }

    fun reset() {
        isAdmin = false
    }
}
