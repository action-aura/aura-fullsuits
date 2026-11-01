"""One error type for the seller rules. `.code` is stable and wordless; the screens turn it into
words with app.i18n_labels.localize_seller_error."""
from __future__ import annotations


class SellerRulesError(ValueError):
    def __init__(self, code: str, **params):
        self.code = code
        self.params = params
        super().__init__(code)
