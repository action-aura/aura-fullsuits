"""Aura Owner -- Flask application factory."""
from __future__ import annotations

from flask import Flask
from flask_cors import CORS

from app.config import get_config
from app.extensions import csrf, db_session, init_db
from app.security.headers import register_security_headers


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__)
    config_cls = get_config(config_name)
    app.config.from_object(config_cls)
    config_cls.validate()

    init_db(
        app.config["SQLALCHEMY_DATABASE_URI"],
        statement_timeout_ms=app.config["DB_STATEMENT_TIMEOUT_MS"],
        lock_timeout_ms=app.config["DB_LOCK_TIMEOUT_MS"],
        idle_in_transaction_timeout_ms=app.config["DB_IDLE_IN_TRANSACTION_TIMEOUT_MS"],
        pool_size=app.config["DB_POOL_SIZE"],
        max_overflow=app.config["DB_MAX_OVERFLOW"],
    )

    from app.observability.logging_config import configure_structured_logging

    configure_structured_logging(app)

    csrf.init_app(app)
    CORS(app, resources={r"/api/*": {"origins": []}})  # no external origins permitted by default
    register_security_headers(app)

    from app.i18n import init_app as init_i18n

    init_i18n(app)

    @app.teardown_appcontext
    def _remove_session(exception=None):
        db_session.remove()

    @app.context_processor
    def _inject_current_staff():
        from app.auth.session import load_current_staff

        return {"staff": load_current_staff()}

    from app.auth import bp as auth_bp
    from app.staff import bp as staff_bp
    from app.catalog import bp as catalog_bp
    from app.customers import bp as customers_bp
    from app.subscriptions import bp as subscriptions_bp
    from app.licensing import bp as licensing_bp
    from app.installations import bp as installations_bp
    from app.dashboard import bp as dashboard_bp
    from app.audit.routes import bp as audit_bp
    from app.releases.routes import bp as releases_bp
    from app.system import bp as system_bp
    from app.licensing_admin import bp as licensing_admin_bp
    from app.commercial_ops.routes import bp as commercial_ops_bp
    from app.commercial_ops.ui_routes import bp as commercial_ops_ui_bp
    from app.health import bp as health_bp
    from app.employees.routes import bp as employees_bp
    from app.employees.self_routes import bp as profile_bp
    from app.api_operations.routes import bp as api_operations_bp
    from app.api_operations.crm import bp as api_operations_crm_bp
    from app.api_operations.commercial_sales import bp as api_operations_commercial_sales_bp
    from app.api_operations.expenses_and_operations import bp as api_operations_expenses_bp
    from app.commercial_sales.routes import bp as commercial_sales_web_bp
    from app.operations_ui.routes import bp as operations_ui_bp
    from app.leads.routes import bp as leads_bp, shared_bp as crm_shared_bp
    from app.locale_routes import bp as locale_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(staff_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(customers_bp)
    app.register_blueprint(subscriptions_bp)
    app.register_blueprint(licensing_bp)
    app.register_blueprint(installations_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(audit_bp)
    app.register_blueprint(releases_bp)
    app.register_blueprint(system_bp)
    app.register_blueprint(licensing_admin_bp)
    app.register_blueprint(commercial_ops_bp)
    app.register_blueprint(commercial_ops_ui_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(employees_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(api_operations_bp)
    app.register_blueprint(api_operations_crm_bp)
    app.register_blueprint(api_operations_commercial_sales_bp)
    app.register_blueprint(api_operations_expenses_bp)
    app.register_blueprint(commercial_sales_web_bp)
    app.register_blueprint(operations_ui_bp)
    app.register_blueprint(leads_bp)
    app.register_blueprint(crm_shared_bp)
    app.register_blueprint(locale_bp)

    if app.config.get("EXTERNAL_API_ENABLED"):
        from app.api.routes import bp as external_api_bp
        from app.api_external.routes import bp as licensing_api_bp

        app.register_blueprint(external_api_bp)
        app.register_blueprint(licensing_api_bp)
        # CSRF tokens are a session-cookie-based browser defense; this API is
        # authenticated by device Ed25519 signatures + nonces instead (Part
        # L), consumed by non-browser clients with no session to carry a
        # token in. Exempting is correct here, not a weakening -- replay.py's
        # nonce+timestamp+signature checks are this API's actual CSRF-
        # equivalent protection.
        csrf.exempt(licensing_api_bp)
    # else: no external API blueprint is ever registered -- there is no route
    # for an external activation/check-in/deactivation request to reach
    # (Part B/R), not merely a disabled check inside one.

    from app.cli import register_cli

    register_cli(app)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "product_code": "AURA_OWNER"}

    return app
