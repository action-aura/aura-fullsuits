"""The words for a PhotoError code. Literal strings so the message extractor sees every one."""
from __future__ import annotations

from flask_babel import gettext as _


def photo_error_message(code: str, **params) -> str:
    if code == "KYC_NOT_READY":
        return _("ID photos cannot be stored on this server yet (no encryption key is set). Nothing was saved. Ask the administrator.")
    if code == "NO_FILE":
        return _("Choose a photo of the ID.")
    if code == "DOCUMENT_TYPE_NOT_ALLOWED":
        return _("The ID photo must be a JPEG or PNG picture.")
    if code == "DOCUMENT_TOO_LARGE":
        return _("The ID photo is too large (at most %(max_mb)s MB).", max_mb=params.get("max_mb", 10))
    if code == "DOCUMENT_EMPTY":
        return _("The ID photo file is empty.")
    if code == "DOCUMENT_CORRUPT":
        return _("The ID photo file is damaged. Take it again.")
    if code == "ALREADY_ON_FILE":
        return _("An ID photo is already on file. Ask a Sales Manager if it has to be replaced.")
    if code == "UPLOAD_CONFLICT":
        return _("Another upload happened at the same moment. Try again.")
    return _("Something went wrong. Nothing was changed.")
