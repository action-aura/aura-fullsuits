"""Canonical permission and role catalog (Part G/AA). Seed-only data -- no
staff users, passwords, or personal data ever originate here."""
from __future__ import annotations

PERMISSIONS: list[tuple[str, str, str]] = [
    # (code, category, description)
    ("staff.view", "STAFF", "View staff accounts"),
    ("staff.create", "STAFF", "Create staff accounts / invitations"),
    ("staff.update", "STAFF", "Update staff account details"),
    ("staff.disable", "STAFF", "Disable a staff account"),
    ("staff.assign_roles", "STAFF", "Change a staff account's roles"),
    ("staff.reset_mfa", "STAFF", "Reset a staff account's MFA enrollment"),
    ("catalog.view", "CATALOG", "View the product/plan/add-on catalog"),
    ("catalog.manage_products", "CATALOG", "Manage products"),
    ("catalog.manage_versions", "CATALOG", "Manage product versions/releases"),
    ("catalog.manage_plans", "CATALOG", "Manage plans"),
    ("catalog.manage_addons", "CATALOG", "Manage add-ons"),
    ("catalog.manage_prices", "CATALOG", "Manage plan prices"),
    ("catalog.manage_entitlements", "CATALOG", "Manage entitlement definitions and mappings"),
    ("customers.view", "CUSTOMERS", "View customer organizations"),
    ("customers.create", "CUSTOMERS", "Create customer organizations"),
    ("customers.update", "CUSTOMERS", "Update customer organizations"),
    ("customers.archive", "CUSTOMERS", "Archive a customer organization"),
    ("customers.manage_contacts", "CUSTOMERS", "Manage customer contacts/addresses"),
    ("customers.manage_notes", "CUSTOMERS", "Manage internal customer notes"),
    ("subscriptions.view", "SUBSCRIPTIONS", "View subscriptions"),
    ("subscriptions.create", "SUBSCRIPTIONS", "Create subscriptions"),
    ("subscriptions.update", "SUBSCRIPTIONS", "Update subscriptions"),
    ("subscriptions.renew", "SUBSCRIPTIONS", "Renew subscriptions"),
    ("subscriptions.cancel", "SUBSCRIPTIONS", "Cancel subscriptions"),
    ("subscriptions.suspend", "SUBSCRIPTIONS", "Suspend subscriptions"),
    ("payments.view", "FINANCE", "View payment records"),
    ("payments.create", "FINANCE", "Create payment records"),
    ("payments.correct", "FINANCE", "Correct/void payment records"),
    ("pricing.view", "FINANCE", "View pricing"),
    ("licenses.view", "LICENSES", "View licenses"),
    ("licenses.create", "LICENSES", "Create license records"),
    ("licenses.issue", "LICENSES", "Issue a license key"),
    ("licenses.suspend", "LICENSES", "Suspend a license"),
    ("licenses.revoke", "LICENSES", "Revoke a license"),
    ("licenses.replace", "LICENSES", "Replace a license"),
    ("licenses.view_masked_key", "LICENSES", "View a license's masked key"),
    ("installations.view", "INSTALLATIONS", "View installations/devices"),
    ("installations.register", "INSTALLATIONS", "Register installations/devices"),
    ("installations.update", "INSTALLATIONS", "Update installations/devices"),
    ("installations.suspend", "INSTALLATIONS", "Suspend an installation"),
    ("audit.view", "AUDIT", "View the audit log"),
    ("audit.export", "AUDIT", "Export audit records"),
    ("system.view", "SYSTEM", "View system settings"),
    ("system.manage_settings", "SYSTEM", "Change system settings"),
    ("system.backup", "SYSTEM", "Trigger an Owner database backup"),
    ("system.restore", "SYSTEM", "Restore the Owner database"),
    # Phase 6 -- Licensing & Activation Service
    ("activation_service.view", "ACTIVATION_SERVICE", "View activation service status/health"),
    ("activation_service.manage", "ACTIVATION_SERVICE", "Enable/configure the external activation API"),
    ("activation_requests.view", "ACTIVATION_SERVICE", "View recent activation/check-in requests"),
    ("activation_requests.review", "ACTIVATION_SERVICE", "Review/annotate activation requests"),
    ("device_keys.view", "ACTIVATION_SERVICE", "View device public-key status"),
    ("device_keys.revoke", "ACTIVATION_SERVICE", "Revoke a device public key"),
    ("signing_keys.view_public_metadata", "ACTIVATION_SERVICE", "View signing-key public metadata"),
    ("signing_keys.manage", "ACTIVATION_SERVICE", "Generate/activate/rotate/revoke signing keys"),
    ("offline_policies.view", "ACTIVATION_SERVICE", "View offline-grace policies"),
    ("offline_policies.manage", "ACTIVATION_SERVICE", "Assign/edit offline-grace policies"),
    ("entitlement_resolution.preview", "ACTIVATION_SERVICE", "Preview entitlement resolution for a license"),
    ("licenses.reactivate", "LICENSES", "Reactivate a suspended license"),
    ("installations.replace_device", "INSTALLATIONS", "Replace a device on an installation"),
    # Phase 8 Milestone 4 -- pilot lifecycle and emergency extensions.
    ("pilots.view", "SUBSCRIPTIONS", "View pilot records"),
    ("pilots.manage", "SUBSCRIPTIONS", "Create/approve/activate/extend/complete/cancel pilot records"),
    # Deliberately NOT assigned to any role below except via the SUPER_ADMIN
    # wildcard -- spec Part N: "Super Admin or narrowly authorized role."
    ("emergency_extensions.view", "SUBSCRIPTIONS", "View emergency commercial extensions"),
    ("emergency_extensions.create", "SUBSCRIPTIONS", "Create an emergency commercial extension"),
    ("emergency_extensions.revoke", "SUBSCRIPTIONS", "Revoke an emergency commercial extension"),
    # Phase 8 Milestone 5 -- manual activation approval and device-slot operations.
    # Deliberately NOT assigned to any role below except via the SUPER_ADMIN
    # wildcard: changing the activation mode itself changes the security
    # posture of every future activation for a product.
    ("activation_policy.manage", "ACTIVATION_SERVICE", "Configure a product's activation mode (automatic/manual-approval/risk-review)"),
    ("pending_activations.view", "ACTIVATION_SERVICE", "View activations awaiting manual approval"),
    ("pending_activations.decide", "ACTIVATION_SERVICE", "Approve or reject a pending activation"),
    ("device_slot_exceptions.view", "INSTALLATIONS", "View temporary device-slot exceptions"),
    ("device_slot_exceptions.manage", "INSTALLATIONS", "Create/revoke a temporary device-slot exception"),
]

# code -> permission codes. SUPER_ADMIN gets every permission automatically
# (StaffUser.is_super_admin bypass in rbac.get_staff_permission_codes) and is
# NOT listed explicitly here to avoid the list silently drifting out of sync
# with PERMISSIONS as new permissions are added.
ROLES: dict[str, dict] = {
    "SUPER_ADMIN": {
        "name": "Super Admin",
        "description": "Full access to every area. Mandatory MFA.",
        "permissions": "*",
    },
    "SALES": {
        "name": "Sales",
        "description": "Customers, contacts, plan/pricing read, subscriptions, license requests.",
        "permissions": [
            "catalog.view", "pricing.view",
            "customers.view", "customers.create", "customers.update", "customers.archive", "customers.manage_contacts", "customers.manage_notes",
            "subscriptions.view", "subscriptions.create", "subscriptions.update", "subscriptions.renew",
            "licenses.view", "licenses.create",
            "installations.view",
            "pilots.view", "pilots.manage",
        ],
    },
    "SUPPORT": {
        "name": "Support",
        "description": "Customer/subscription/license read, installation and device management.",
        "permissions": [
            "catalog.view",
            "customers.view", "customers.manage_notes",
            "subscriptions.view",
            "licenses.view",
            "installations.view", "installations.register", "installations.update", "installations.suspend",
            "installations.replace_device",
            "activation_service.view", "activation_requests.view", "device_keys.view",
            "pilots.view",
            "pending_activations.view", "pending_activations.decide",
            "device_slot_exceptions.view", "device_slot_exceptions.manage",
            # No signing-key management, no plan/entitlement changes, no license-secret issuance (Part S).
        ],
    },
    "FINANCE": {
        "name": "Finance",
        "description": "Customer read, subscription/renewal records, pricing, payment records.",
        "permissions": [
            "catalog.view", "catalog.manage_prices", "pricing.view",
            "customers.view",
            "subscriptions.view", "subscriptions.renew",
            "payments.view", "payments.create", "payments.correct",
        ],
    },
    "VIEWER": {
        "name": "Viewer",
        "description": "Read-only access to approved non-sensitive internal metadata.",
        "permissions": [
            "catalog.view", "customers.view", "subscriptions.view", "licenses.view", "installations.view",
            "activation_service.view", "activation_requests.view", "device_keys.view",
            "signing_keys.view_public_metadata", "offline_policies.view",
            "pilots.view",
            "pending_activations.view", "device_slot_exceptions.view",
        ],
    },
}
