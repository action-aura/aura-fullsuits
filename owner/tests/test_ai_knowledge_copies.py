"""The staff helper's knowledge files (owner/app/ai/knowledge/).

The Docker image copies only owner/, so the sales docs it answers from are
COPIES of docs/sales/ar/*. A copy that drifts from its source means the helper
quotes yesterday's sales rules to a seller; this test fails the build instead.
It compares BYTES, not decoded text: a copy re-saved with CRLF endings or a BOM
would pass a text comparison and still differ."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.ai import prompts

OWNER_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = OWNER_DIR.parent
KNOWLEDGE = OWNER_DIR / "app" / "ai" / "knowledge"
COPIES = ("seller-guide.md", "cheat-sheet.md", "guide-2026-10-27.md")


@pytest.mark.parametrize("name", COPIES)
def test_each_copy_is_byte_identical_to_its_source(name):
    source = REPO_ROOT / "docs" / "sales" / "ar" / name
    copy = KNOWLEDGE / name
    assert source.is_file(), f"the source doc moved or was renamed: {source}"
    assert copy.is_file(), f"knowledge copy missing: {copy}"
    assert copy.read_bytes() == source.read_bytes(), (
        f"{name} differs from docs/sales/ar/{name}; re-copy it with shutil.copyfile (not write_text, "
        "which can flip LF to CRLF)"
    )


def test_owner_screens_names_only_screens_the_nav_really_has():
    nav = (OWNER_DIR / "app" / "templates" / "layout" / "_nav_model.html").read_text(encoding="utf-8")
    nav_labels = set(re.findall(r"_\('([^']+)'\)", nav))
    guide = (KNOWLEDGE / "owner_screens.md").read_text(encoding="utf-8")
    # Every English screen name the guide puts in bold or "Name (" form must be a real nav label,
    # except the few that are not nav entries (the page-level headings listed explicitly).
    named = set(re.findall(r"\*\*([A-Za-z][A-Za-z /.&-]+?)(?: \(|\*\*|:)", guide))
    not_nav_entries = {"Search", "Dashboard"}  # Search is the top-bar box; Dashboard is also a nav label
    unknown = {n for n in named if n not in nav_labels and n not in not_nav_entries and n.rstrip(".") not in nav_labels}
    # Group headings and page sections written in bold are allowed; screens are not.
    group_headings = {"Overview", "CRM", "Sales", "Finance / Operations", "Licensing", "Management", "System"}
    assert not (unknown - group_headings), f"owner_screens.md names screens the nav does not have: {sorted(unknown - group_headings)}"
    for label in ("Leads", "Customers", "Sell a licence", "Sales to confirm", "Audit Log", "Backups", "Crash reports"):
        assert label in nav_labels and label in guide


def test_system_prompt_contains_every_knowledge_file_and_is_byte_stable():
    prompts.staff_help_system.cache_clear()
    first = prompts.staff_help_system()
    prompts.staff_help_system.cache_clear()
    second = prompts.staff_help_system()
    assert first == second, "the help system prompt must not vary between calls (it is the cacheable prefix)"
    for name in prompts.KNOWLEDGE_FILES:
        body = (KNOWLEDGE / name).read_text(encoding="utf-8")
        assert body in first, f"{name} is not in the system prompt"
    assert "Never ask for, repeat or store customer personal data" in first
    assert not re.search(r"20\d\d-\d\d-\d\d \d\d:\d\d", first.split("REFERENCE MATERIAL:")[0]), "no timestamp in the rules part"
