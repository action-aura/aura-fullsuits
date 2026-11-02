"""One error type for personal-data requests. The code is machine text; the words a person reads
live in app/i18n_labels.py::localize_data_request_error (English + Arabic)."""
from __future__ import annotations


class DataRequestError(Exception):
    def __init__(self, code: str, **params):
        super().__init__(code)
        self.code = code
        self.params = params
