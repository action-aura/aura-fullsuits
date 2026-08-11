"""Phase 9.5B-R2 -- regression guard for the real mobile-collapse defect
found via Playwright at 390x844: every <table> in the newly translated
template set must carry class="responsive-table", or the CSS rule in
layout/base.html (`table.responsive-table thead { display: none; }` etc.,
scoped to that class) never applies and the table renders as a cramped,
unreadable grid on narrow viewports instead of stacked cards."""
from __future__ import annotations

import os
import re

OWNER_ROOT = os.path.dirname(os.path.dirname(__file__))
TEMPLATES_ROOT = os.path.join(OWNER_ROOT, "app", "templates")

NEW_DIRS = (
    "dashboard", "audit", "catalog", "customers", "installations",
    "licensing", "licensing_admin", "staff", "subscriptions", "system",
    "commercial_ops",
)

_BARE_TABLE_RE = re.compile(r"<table(?![^>]*\bclass=)[^>]*>")


def test_every_table_in_the_newly_translated_templates_is_responsive():
    violations = []
    for d in NEW_DIRS:
        dir_path = os.path.join(TEMPLATES_ROOT, d)
        if not os.path.isdir(dir_path):
            continue
        for name in os.listdir(dir_path):
            if not name.endswith(".html"):
                continue
            path = os.path.join(dir_path, name)
            with open(path, "r", encoding="utf-8") as f:
                html = f.read()
            for match in _BARE_TABLE_RE.finditer(html):
                violations.append((os.path.relpath(path, OWNER_ROOT), match.group(0)))
    assert violations == [], f"{len(violations)} <table> without class=\"responsive-table\": {violations}"


def test_every_table_with_a_class_uses_the_responsive_table_class():
    """A table that has some other class but not responsive-table would
    pass the bare-tag check above and still be broken -- checked
    separately."""
    violations = []
    for d in NEW_DIRS:
        dir_path = os.path.join(TEMPLATES_ROOT, d)
        if not os.path.isdir(dir_path):
            continue
        for name in os.listdir(dir_path):
            if not name.endswith(".html"):
                continue
            path = os.path.join(dir_path, name)
            with open(path, "r", encoding="utf-8") as f:
                html = f.read()
            for match in re.finditer(r'<table\s+class="([^"]*)"', html):
                if "responsive-table" not in match.group(1).split():
                    violations.append((os.path.relpath(path, OWNER_ROOT), match.group(0)))
    assert violations == [], f"{len(violations)} <table class=...> missing responsive-table: {violations}"
