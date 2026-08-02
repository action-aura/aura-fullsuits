"""Phase 9.5B-R -- the one canonical Owner internationalization authority
(Non-Negotiable Principle 1). Every locale-resolution decision in the
codebase goes through select_locale() below; nothing else re-implements
locale negotiation, and no template hardcodes a per-feature translation
dictionary.

Locale is presentation-only (Non-Negotiable Principle 3) -- nothing here
ever touches authorization, employee lifecycle, financial values, or any
stored enum. select_locale() itself never trusts raw client input: the
supported-locale allowlist (app.config["LANGUAGES"]) is the only source of
truth for what a valid locale code is (Non-Negotiable Principle 5).
"""
from __future__ import annotations

from flask import current_app, g, request, session

from app.extensions import babel

LOCALE_COOKIE_NAME = "owner_locale"
LOCALE_SESSION_KEY = "locale"
LOCALE_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365  # 1 year -- a presentation preference, not a security token

# RTL is a layout mode (Non-Negotiable Principle 6), not a per-string
# property -- one small, explicit set, consulted by current_direction()/
# is_rtl(), never inferred from string content.
RTL_LOCALES = {"ar"}


def supported_locales() -> list[str]:
    return list(current_app.config["LANGUAGES"].keys())


def is_supported_locale(code: str | None) -> bool:
    return bool(code) and code in current_app.config["LANGUAGES"]


def select_locale() -> str:
    """Precedence (governing spec's own recommended order):
    1. An explicit language switch already validated and stashed in Flask's
       own signed session by the /locale/<code> route this request or a
       prior one -- never re-read raw from the query string here, so this
       function is the one place trust is granted after validation.
    2. The authenticated staff account's persisted locale preference.
    3. A safe, previously-set locale cookie (anonymous/pre-auth continuity).
    4. A supported Accept-Language match.
    5. The application default (en).
    """
    session_locale = session.get(LOCALE_SESSION_KEY)
    if is_supported_locale(session_locale):
        return session_locale

    from app.auth.session import load_current_staff  # local import avoids a package-load cycle

    staff = load_current_staff()
    if staff is not None and is_supported_locale(getattr(staff, "locale", None)):
        return staff.locale

    cookie_locale = request.cookies.get(LOCALE_COOKIE_NAME)
    if is_supported_locale(cookie_locale):
        return cookie_locale

    best = request.accept_languages.best_match(supported_locales())
    if best:
        return best

    return current_app.config["BABEL_DEFAULT_LOCALE"]


def current_direction(locale_code: str | None = None) -> str:
    code = locale_code or select_locale()
    return "rtl" if code in RTL_LOCALES else "ltr"


def is_rtl(locale_code: str | None = None) -> bool:
    return current_direction(locale_code) == "rtl"


def init_app(app) -> None:
    babel.init_app(app, locale_selector=select_locale)

    from app.i18n_format import format_owner_date, format_owner_datetime, format_owner_number
    from app.i18n_labels import (
        account_status_label,
        activation_event_result_label,
        activation_event_type_label,
        activation_mode_label,
        addon_availability_status_label,
        audit_action_label,
        audit_result_label,
        backup_status_label,
        billing_model_label,
        customer_status_label,
        device_key_status_label,
        duplicate_review_marker_label,
        followup_status_label,
        interaction_type_label,
        lead_priority_label,
        lead_source_label,
        lead_status_label,
        location_source_label,
        location_verification_label,
        note_visibility_label,
        emergency_extension_status_label,
        employment_status_label,
        generic_audit_action_label,
        health_status_label,
        installation_status_label,
        invitation_status_label,
        license_status_label,
        mfa_status_label,
        notification_severity_label,
        notification_status_label,
        payment_status_label,
        pending_activation_status_label,
        permission_category_label,
        pilot_conversion_decision_label,
        pilot_status_label,
        plan_lifecycle_status_label,
        product_commercial_status_label,
        presence_label,
        renewal_date_rule_label,
        renewal_status_label,
        role_label,
        session_status_label,
        signing_key_status_label,
        subscription_status_label,
        timeline_category_label,
        quote_status_label,
        sales_order_status_label,
        commercial_invoice_status_label,
        commercial_refund_status_label,
        commercial_approval_status_label,
        commission_entry_status_label,
        commission_payout_batch_status_label,
        localize_commercial_sales_error,
        localize_commission_error,
    )

    # Domain-label and formatting helpers are Jinja globals (not filters) --
    # exposed once, here, reused by every template rather than each screen
    # importing/registering its own (Non-Negotiable Principle 1/10).
    app.jinja_env.globals.update(
        employment_status_label=employment_status_label,
        presence_label=presence_label,
        role_label=role_label,
        account_status_label=account_status_label,
        session_status_label=session_status_label,
        mfa_status_label=mfa_status_label,
        invitation_status_label=invitation_status_label,
        audit_action_label=audit_action_label,
        audit_result_label=audit_result_label,
        activation_event_result_label=activation_event_result_label,
        activation_event_type_label=activation_event_type_label,
        activation_mode_label=activation_mode_label,
        permission_category_label=permission_category_label,
        format_owner_date=format_owner_date,
        format_owner_datetime=format_owner_datetime,
        format_owner_number=format_owner_number,
        subscription_status_label=subscription_status_label,
        license_status_label=license_status_label,
        installation_status_label=installation_status_label,
        customer_status_label=customer_status_label,
        lead_status_label=lead_status_label,
        lead_source_label=lead_source_label,
        lead_priority_label=lead_priority_label,
        interaction_type_label=interaction_type_label,
        followup_status_label=followup_status_label,
        note_visibility_label=note_visibility_label,
        location_source_label=location_source_label,
        location_verification_label=location_verification_label,
        duplicate_review_marker_label=duplicate_review_marker_label,
        renewal_status_label=renewal_status_label,
        renewal_date_rule_label=renewal_date_rule_label,
        pilot_status_label=pilot_status_label,
        pilot_conversion_decision_label=pilot_conversion_decision_label,
        notification_severity_label=notification_severity_label,
        notification_status_label=notification_status_label,
        pending_activation_status_label=pending_activation_status_label,
        payment_status_label=payment_status_label,
        product_commercial_status_label=product_commercial_status_label,
        plan_lifecycle_status_label=plan_lifecycle_status_label,
        addon_availability_status_label=addon_availability_status_label,
        backup_status_label=backup_status_label,
        billing_model_label=billing_model_label,
        timeline_category_label=timeline_category_label,
        signing_key_status_label=signing_key_status_label,
        device_key_status_label=device_key_status_label,
        emergency_extension_status_label=emergency_extension_status_label,
        generic_audit_action_label=generic_audit_action_label,
        health_status_label=health_status_label,
        quote_status_label=quote_status_label,
        sales_order_status_label=sales_order_status_label,
        commercial_invoice_status_label=commercial_invoice_status_label,
        commercial_refund_status_label=commercial_refund_status_label,
        commercial_approval_status_label=commercial_approval_status_label,
        commission_entry_status_label=commission_entry_status_label,
        commission_payout_batch_status_label=commission_payout_batch_status_label,
        localize_commercial_sales_error=localize_commercial_sales_error,
        localize_commission_error=localize_commission_error,
    )

    @app.context_processor
    def _inject_i18n_globals():
        locale = select_locale()
        return {
            "current_locale": locale,
            "current_direction": current_direction(locale),
            "is_rtl": is_rtl(locale),
            "supported_locales": current_app.config["LANGUAGES"],
        }

    @app.template_filter("bidi_isolate")
    def _bidi_isolate(value):
        """Phase 9.5B-R Milestone 7 -- wraps an LTR identifier (email, UUID,
        employee number, correlation ID, phone) so it renders correctly and
        stays copyable inside an RTL (Arabic) page. <bdi> isolates the value
        from the surrounding paragraph's bidi algorithm without needing to
        know the value's own direction; dir="ltr" pins it explicitly since
        every identifier this filter is used for is always LTR-shaped
        (never itself translated or reversed)."""
        if value is None:
            return ""
        from markupsafe import Markup, escape

        return Markup(f'<bdi dir="ltr">{escape(value)}</bdi>')
