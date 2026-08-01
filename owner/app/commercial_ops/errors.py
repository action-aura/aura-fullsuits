"""Phase 9.5B-R3 -- shared stable-code exception base for the commercial_ops
service layer.

Non-Negotiable: service/domain code must never depend on request context
(Phase 9.5B-R2 found this the hard way -- gettext() inside a service raise
site broke every non-HTTP caller with "RuntimeError: Working outside of
request context"). Every raise site here provides a stable, English-only
`code` plus structured `params`. `str(exc)` (used by CLI output, logs, and
the JSON API's `detail` field in commercial_ops/routes.py) is always a
real, readable, English diagnostic sentence -- deliberately never
translated, since it must remain constructible with zero request/session
state. A user-facing Jinja route localizes via `exc.code`/`exc.params` at
the presentation boundary instead (see `app.i18n_labels`'s
`localize_*_error` functions and their call sites in
`commercial_ops/ui_routes.py`)."""
from __future__ import annotations


class StableCodeError(ValueError):
    _MESSAGES: dict[str, str] = {}

    def __init__(self, code: str, **params):
        self.code = code
        self.params = params
        template = self._MESSAGES.get(code, code)
        super().__init__(template.format(**params) if params else template)
