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
    # Phase 5 prerequisite #3 -- sync quarantine console
    # (docs/launch-readiness/phase5-prerequisites.md section 3). Same
    # view/mutate split precedent as device_keys.view/device_keys.revoke:
    # view is granted to SUPPORT below, replay/discard are deliberately
    # NOT granted to any role except via the SUPER_ADMIN wildcard -- both
    # actions mutate the sync ledger (a replayed event becomes a real,
    # permanent SyncEvent; a discard is a considered "never apply this"
    # decision), the same class of security-relevant action as revoking a
    # device key.
    ("sync_quarantine.view", "ACTIVATION_SERVICE", "View quarantined sync events"),
    ("sync_quarantine.replay", "ACTIVATION_SERVICE", "Replay a quarantined sync event"),
    ("sync_quarantine.discard", "ACTIVATION_SERVICE", "Discard a quarantined sync event"),
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
    # Phase 9.5A -- commercial-operations foundation (employees, leads, sales
    # documents, commissions, expenses, device policy, reports). Every
    # permission here is additive; nothing above is renamed or removed.
    ("employees.view_own", "EMPLOYEES", "View own employee profile"),
    ("employees.view_all", "EMPLOYEES", "View all employee profiles"),
    ("employees.create", "EMPLOYEES", "Create an employee profile"),
    ("employees.update", "EMPLOYEES", "Update an employee profile"),
    ("employees.suspend", "EMPLOYEES", "Suspend an employee"),
    ("employees.terminate", "EMPLOYEES", "Terminate an employee"),
    ("employees.assign_role", "EMPLOYEES", "Assign roles to an employee's staff account"),
    ("employees.manage_commission_plan", "EMPLOYEES", "Assign/change an employee's commission plan"),
    ("employees.view_presence", "EMPLOYEES", "View employee online/presence status"),
    ("leads.create", "SALES_PIPELINE", "Create a lead"),
    ("leads.view_own", "SALES_PIPELINE", "View own (created or assigned) leads"),
    ("leads.view_all", "SALES_PIPELINE", "View all leads"),
    ("leads.update_own", "SALES_PIPELINE", "Update own (created or assigned) leads"),
    ("leads.update_all", "SALES_PIPELINE", "Update any lead"),
    ("leads.assign", "SALES_PIPELINE", "Assign/reassign a lead"),
    ("leads.convert", "SALES_PIPELINE", "Convert a lead to a customer"),
    ("leads.archive", "SALES_PIPELINE", "Archive a lead"),
    ("customers.view_own", "CUSTOMERS", "View own (assigned) customers"),
    ("customers.view_all", "CUSTOMERS", "View all customers"),
    ("customers.update_own", "CUSTOMERS", "Update own (assigned) customers"),
    ("customers.update_all", "CUSTOMERS", "Update any customer"),
    ("customers.assign", "CUSTOMERS", "Assign/reassign a customer"),
    ("customers.capture_location", "CUSTOMERS", "Capture a lead/customer location"),
    ("customers.verify_location", "CUSTOMERS", "Verify/correct another employee's captured location"),
    ("quotes.create", "COMMERCIAL_SALES", "Create a quote"),
    ("quotes.approve", "COMMERCIAL_SALES", "Approve/send a quote"),
    ("orders.create", "COMMERCIAL_SALES", "Create a sales order"),
    ("orders.approve", "COMMERCIAL_SALES", "Confirm a sales order"),
    ("invoices.create", "COMMERCIAL_SALES", "Create a commercial invoice"),
    ("invoices.issue", "COMMERCIAL_SALES", "Issue a commercial invoice"),
    ("pricing.override", "COMMERCIAL_SALES", "Override a catalog price on a commercial-sales line item"),
    ("payments.confirm", "FINANCE", "Confirm a payment (distinct from recording one)"),
    ("refunds.create", "COMMERCIAL_SALES", "Create a refund record"),
    ("refunds.approve", "COMMERCIAL_SALES", "Approve/pay a refund"),
    ("commissions.view_own", "COMMISSIONS", "View own commission ledger"),
    ("commissions.view_all", "COMMISSIONS", "View all employees' commissions"),
    ("commissions.calculate", "COMMISSIONS", "Manually re-run commission eligibility evaluation"),
    ("commissions.approve", "COMMISSIONS", "Approve a commission ledger entry"),
    ("commissions.pay", "COMMISSIONS", "Approve a commission payout batch"),
    ("commissions.reverse", "COMMISSIONS", "Reverse a commission ledger entry"),
    ("expenses.create", "EXPENSES", "Create/submit an expense"),
    ("expenses.view_own", "EXPENSES", "View own submitted expenses"),
    ("expenses.view_all", "EXPENSES", "View all expenses"),
    ("expenses.approve", "EXPENSES", "Approve an expense"),
    ("expenses.pay", "EXPENSES", "Mark an expense paid"),
    ("expenses.void", "EXPENSES", "Void an expense"),
    ("device_policy.view", "LICENSING_OPERATIONS", "View device-policy profiles/overrides"),
    ("device_policy.manage", "LICENSING_OPERATIONS", "Create/edit device-policy profiles/overrides"),
    ("dashboard.view_own", "REPORTS", "View own-scoped dashboard"),
    ("dashboard.view_all", "REPORTS", "View the management dashboard"),
    ("reports.view_own", "REPORTS", "View own-scoped daily reports"),
    ("reports.view_all", "REPORTS", "View global daily reports"),
    ("reports.regenerate_daily", "REPORTS", "Manually regenerate a daily activity snapshot"),
    ("management_notes.view", "MANAGEMENT_COLLABORATION", "View shared management notes visible to this account"),
    ("management_notes.manage", "MANAGEMENT_COLLABORATION", "Create/edit/archive shared management notes"),
    ("security_sessions.revoke", "STAFF", "Revoke a staff/employee session"),
    # Phase 9.5E -- Expense payees.
    ("expenses.manage_payees", "EXPENSES", "Create/deactivate expense payees"),
    # Phase 9.5E -- Daily Cash Closing.
    ("cash_closing.prepare", "CASH_CLOSING", "Create/submit a daily cash closing"),
    ("cash_closing.view_own", "CASH_CLOSING", "View cash closings this account prepared"),
    ("cash_closing.view_all", "CASH_CLOSING", "View all cash closings"),
    ("cash_closing.approve", "CASH_CLOSING", "Approve/reject a submitted cash closing"),
    ("cash_closing.reopen", "CASH_CLOSING", "Reopen an approved/closed cash closing"),
    ("cash_closing.adjust", "CASH_CLOSING", "Create a cash closing adjustment"),
    ("cash_closing.approve_adjustment", "CASH_CLOSING", "Approve a cash closing adjustment"),
    # Phase 9.5E -- scheduled report snapshots (reports.view_own/view_all and
    # reports.regenerate_daily are Phase 9.5A permissions, reused as-is).
    ("report_snapshots.view", "REPORTS", "View scheduled report snapshots"),
    ("report_snapshots.regenerate", "REPORTS", "Manually regenerate a report snapshot"),
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
            # Phase 9.5A -- a SALES employee works their own leads/customers/
            # sales documents and sees their own commission/dashboard/report
            # data; deliberately NOT granted leads.view_all/update_all,
            # customers.view_all/update_all, any commissions.* beyond
            # view_own, any expenses.* beyond create, invoices.issue,
            # pricing.override, device_policy.manage, or anything financial-
            # approval-shaped (Non-Negotiable Principle 4/8: employees select
            # from admin-managed prices, they don't authorize their own
            # overrides or approve their own money).
            "employees.view_own", "employees.view_presence",
            "leads.create", "leads.view_own", "leads.update_own", "leads.convert",
            "customers.view_own", "customers.update_own", "customers.capture_location",
            "quotes.create", "orders.create", "invoices.create",
            # Phase 9.5D Milestone 18 -- real gap found and closed:
            # payments.create was FINANCE-only, but submit_payment()'s own
            # docstring and payment-maker-checker-policy.md both describe
            # this as the sales-employee-facing "I received this payment,
            # please confirm it" action -- the M10 doc explicitly deferred
            # this exact decision to "Milestone 16/19 ... at the route/
            # permission-grant level". payments.confirm remains FINANCE-only
            # (never granted here) -- the maker-checker separation this
            # phase requires everywhere else.
            "payments.create",
            "commissions.view_own",
            "expenses.create", "expenses.view_own",
            "device_policy.view",
            "dashboard.view_own", "reports.view_own",
            # Phase 9.5E -- a SALES employee reads shared notes addressed to
            # them (ALL_STAFF/SPECIFIC_EMPLOYEES visibility resolves this at
            # the query layer, not via a separate role); never management.manage
            # (creating/assigning notes is a management action) and never
            # cash_closing.*/report_snapshots.* (financial-authority-shaped,
            # same reasoning as expenses.approve staying FINANCE-only).
            "management_notes.view",
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
            # Phase 5 prerequisite #3 -- view-only, same precedent as
            # device_keys.view above: SUPPORT can see what's quarantined to
            # help diagnose a customer's stuck sync, but replay/discard
            # (mutating the ledger) stay SUPER_ADMIN-only.
            "sync_quarantine.view",
            # No signing-key management, no plan/entitlement changes, no license-secret issuance (Part S).
            # Phase 9.5A -- Support reads customer/lead context to help, but
            # does not own the sales pipeline or approve money.
            "customers.view_own", "leads.view_own",
            "device_policy.view",
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
            # Phase 9.5A -- Finance is the money-authorization role: confirms
            # payments, issues invoices, approves refunds, approves/pays
            # commissions and expenses. Deliberately NOT leads.*/customers.*
            # ownership permissions (Finance isn't a sales-pipeline role) and
            # NOT device_policy.manage (a commercial-config, not financial,
            # authority -- SUPER_ADMIN only).
            "payments.confirm", "invoices.issue", "refunds.create", "refunds.approve",
            # Phase 9.5D Milestone 16 -- real gap found and closed: quotes.approve/
            # orders.approve were pre-seeded (Phase 9.5A) but never granted to any
            # role, so no one but SUPER_ADMIN could ever confirm a Sales Order or
            # approve a pricing exception once routes exist. Order confirmation
            # ("this sale is real and committed") and quote/exception approval are
            # the same category of money-authorization decision FINANCE already
            # holds for invoices/refunds/commissions -- granted here, not to SALES
            # (which only ever gets *.create, never *.approve, on any commercial
            # document -- see commercial-sales-sod-matrix.md).
            "quotes.approve", "orders.approve",
            # pricing.override is deliberately NOT granted here (or to any role
            # below) -- like activation_policy.manage/signing_keys.manage, it's
            # SUPER_ADMIN-only via the wildcard; a price exception that needs an
            # outright catalog-price bypass (not just a discount approval) is a
            # commercial-authority decision, not a finance-confirmation one.
            "commissions.view_all", "commissions.approve", "commissions.pay", "commissions.reverse",
            "expenses.view_all", "expenses.approve", "expenses.pay", "expenses.void",
            "customers.view_all",
            "dashboard.view_all", "reports.view_all",
            # Phase 9.5E -- real gaps closed, same class as Milestone 16/18's
            # own findings: management_notes.* and reports.regenerate_daily
            # were pre-seeded (Phase 9.5A) but granted to no role at all.
            # Finance is the money-authorization + operational-reporting role
            # (already holds reports.view_all/dashboard.view_all), so it also
            # owns Cash Closing preparation/approval, payee management, and
            # scheduled-report regeneration -- the same category of authority
            # it already exercises for invoices/refunds/commissions/expenses.
            # management_notes.manage is deliberately NOT granted here -- a
            # real Milestone 26 regression caught this module's own earlier
            # grant of it violating Phase 9.5A's explicit, documented
            # SUPER_ADMIN-only commitment for that exact permission
            # (sensitive-action-control-matrix.md: "management notes being
            # inherently a management-only concept"; enforced by
            # test_phase9_5a_rbac_restrictions.py::test_sensitive_permissions_not_granted_below_super_admin).
            # Finance keeps management_notes.view (reading notes addressed
            # to it is not the same authority as creating/editing/archiving
            # them); see management-notes-manage-rbac-correction.md.
            "management_notes.view",
            "reports.regenerate_daily",
            "expenses.manage_payees",
            "cash_closing.prepare", "cash_closing.view_own", "cash_closing.view_all",
            "cash_closing.approve", "cash_closing.reopen", "cash_closing.adjust", "cash_closing.approve_adjustment",
            "report_snapshots.view", "report_snapshots.regenerate",
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
            # Phase 9.5A -- read-only, non-sensitive only: no commissions.*
            # (personal earnings data), no expenses.* (financial-sensitive),
            # no employees.* beyond nothing (personal HR data).
            "leads.view_all", "customers.view_all",
            "device_policy.view",
            "dashboard.view_all", "reports.view_all",
            # Phase 9.5E -- report_snapshots.view only: published operational
            # figures, same sensitivity class as reports.view_all it already
            # holds. No cash_closing.*/expenses.manage_payees (financial-
            # authority-shaped) and no management_notes.* (may carry
            # sensitive management discussion, matching this role's own
            # "no personal HR data" boundary above).
            "report_snapshots.view",
        ],
    },
}
