"""Installable web app (PWA) support: manifest, service worker, offline page.
See routes.py for the safety policy."""
from app.pwa.routes import bp

__all__ = ["bp"]
