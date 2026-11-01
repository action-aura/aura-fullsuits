"""One error type for sanctions and terminations. The code is machine text; the words a person
reads live in app/i18n_labels.py::localize_discipline_error (English + Arabic)."""
from __future__ import annotations


class DisciplineError(Exception):
    def __init__(self, code: str, **params):
        super().__init__(code)
        self.code = code
        self.params = params
