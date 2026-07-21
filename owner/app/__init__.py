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

    init_db(app.config["SQLALCHEMY_DATABASE_URI"])

    csrf.init_app(app)
    CORS(app, resources={r"/api/*": {"origins": []}})  # no external origins permitted by default
    register_security_headers(app)

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

    if app.config.get("EXTERNAL_API_ENABLED"):
        from app.api.routes import bp as external_api_bp

        app.register_blueprint(external_api_bp)
    # else: no external API blueprint is ever registered -- there is no route
    # for an external activation request to reach (Part R), not merely a
    # disabled check inside one.

    from app.cli import register_cli

    register_cli(app)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "product_code": "AURA_OWNER"}

    return app
