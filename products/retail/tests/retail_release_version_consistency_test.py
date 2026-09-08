"""One release version, declared in eight files, checked by nothing until now.

WHY THIS TEST EXISTS

`docs/release/versioning-policy.md` lists where version identity lives and says
plainly that the copies are "kept in sync manually". It also records, in its
own words, what manual syncing already cost once: the installer's
`#define AppVersion` was "missed by this document until Phase 7's baseline
audit found it still reading `1.0.0-rc.1` after every other source had already
been listed here."

That is ENGINEERING.md section 4 exactly -- a single authoritative value with
more than one writer and nothing arbitrating. The failure is silent by
construction: a stale `.iss` still compiles, still installs, and produces an
installer whose filename and Add/Remove Programs entry disagree with the
`/api/version` the app itself serves. Nobody notices until a customer reports
a version that does not exist.

WHAT IS CHECKED

1. All eight declarations agree, and the failure names every file and its value
   rather than only that they differ.
2. `version_info.txt`'s numeric fields agree with the semver they sit beside.
   Windows version resources carry BOTH a 4-tuple (`filevers`, `prodvers`) and
   free-text strings, and the tuple cannot hold `-rc.N` -- so the two halves of
   the same file can drift apart on their own, independently of the other seven
   files. They did not, but nothing said so.

WHAT IS DELIBERATELY NOT CHECKED

Android `versionCode`. It is a monotonic integer that must rise on every store
upload and is NOT derivable from the semver, so asserting a relationship
between them would be inventing a rule the policy does not state. Its own
constraint -- never reused, never decreasing -- is a different test against
release history, not against this file.

Run standalone, one file per process, like every test here (AUDIT-010):

    python -m pytest products/retail/tests/retail_release_version_consistency_test.py
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

# Each entry: a label, the file, and the pattern whose first group is the
# version. Taken from versioning-policy.md's "Where version identity lives"
# list, plus the two `.iss` files that list originally missed.
DECLARATIONS = [
    ("retail backend APP_VERSION",
     "products/retail/backend/config.py",
     r"^APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]"),
    ("clinic backend APP_VERSION",
     "products/clinic/backend/config.py",
     r"^APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]"),
    ("retail Android versionName",
     "android/aura-retail/app/build.gradle",
     r"versionName\s+\"([^\"]+)\""),
    ("clinic Android versionName",
     "android/aura-clinic/app/build.gradle",
     r"versionName\s+\"([^\"]+)\""),
    ("retail installer AppVersion",
     "products/retail/packaging/aura_retail_setup.iss",
     r"#define\s+AppVersion\s+\"([^\"]+)\""),
    ("clinic installer AppVersion",
     "products/clinic/packaging/aura_clinic_setup.iss",
     r"#define\s+AppVersion\s+\"([^\"]+)\""),
    ("retail exe ProductVersion",
     "products/retail/packaging/version_info.txt",
     r"StringStruct\(u'ProductVersion',\s*u'([^']+)'\)"),
    ("clinic exe ProductVersion",
     "products/clinic/packaging/version_info.txt",
     r"StringStruct\(u'ProductVersion',\s*u'([^']+)'\)"),
]

VERSION_RESOURCES = [
    ("retail", "products/retail/packaging/version_info.txt"),
    ("clinic", "products/clinic/packaging/version_info.txt"),
]


def _read(relative: str) -> str:
    path = ROOT / relative
    if not path.exists():
        pytest.fail(
            f"{relative} does not exist. It is named in "
            f"docs/release/versioning-policy.md as a place the release version "
            f"lives; if it moved, update that document and this list together."
        )
    return path.read_text(encoding="utf-8")


def _declared() -> list[tuple[str, str, str]]:
    """(label, relative path, version) for every declaration site."""
    found = []
    for label, relative, pattern in DECLARATIONS:
        text = _read(relative)
        match = re.search(pattern, text, re.MULTILINE)
        assert match, (
            f"{label}: no version found in {relative} using {pattern!r}. "
            f"Either the declaration moved or its shape changed -- this test "
            f"reads the file rather than a copy, so a silent miss here would "
            f"make the whole check vacuous."
        )
        found.append((label, relative, match.group(1)))
    return found


def test_every_declaration_of_the_release_version_agrees():
    found = _declared()
    versions = {version for _, _, version in found}
    assert len(versions) == 1, (
        "The release version disagrees across its declaration sites. "
        "versioning-policy.md keeps these in sync by hand, and has already "
        "lost one to that once (the installer sat on 1.0.0-rc.1 after every "
        "other source moved). Every site, as found:\n"
        + "\n".join(f"    {v:<16} {label}  ({rel})" for label, rel, v in found)
    )


def test_the_version_is_shaped_the_way_the_policy_says():
    # MAJOR.MINOR.PATCH with an optional -rc.N. Asserted so a typo that lands
    # in all eight files at once -- the one shape the agreement test above
    # cannot see -- still fails something.
    _, _, version = _declared()[0]
    assert re.fullmatch(r"\d+\.\d+\.\d+(-rc\.\d+)?", version), (
        f"{version!r} is not MAJOR.MINOR.PATCH[-rc.N] as "
        f"docs/release/versioning-policy.md prescribes."
    )


@pytest.mark.parametrize("product,relative", VERSION_RESOURCES)
def test_windows_version_resource_numbers_match_its_own_semver(product, relative):
    """A Windows version resource states the version twice, in two formats.

    `filevers`/`prodvers` are 4-tuples of integers and cannot carry `-rc.N`;
    `FileVersion`/`ProductVersion` are free text. So this file can contradict
    ITSELF without contradicting any of the other seven, which is why this is
    a separate check rather than part of the one above.
    """
    text = _read(relative)
    semver = re.search(
        r"StringStruct\(u'ProductVersion',\s*u'([^']+)'\)", text).group(1)
    numeric = tuple(int(n) for n in semver.split("-")[0].split("."))

    for field in ("filevers", "prodvers"):
        raw = re.search(rf"{field}=\(([^)]+)\)", text)
        assert raw, f"{relative}: no {field} tuple found"
        tup = tuple(int(n.strip()) for n in raw.group(1).split(","))
        assert tup[:3] == numeric, (
            f"{relative}: {field}={tup} does not match ProductVersion "
            f"{semver!r}. The first three numbers must be the semver's."
        )

    file_version = re.search(
        r"StringStruct\(u'FileVersion',\s*u'([^']+)'\)", text).group(1)
    assert tuple(int(n) for n in file_version.split(".")[:3]) == numeric, (
        f"{relative}: FileVersion {file_version!r} does not match "
        f"ProductVersion {semver!r}."
    )


def test_this_test_reads_all_eight_sites_the_policy_names():
    # Anti-vacuity. If someone adds a ninth declaration site to the policy and
    # not to this file, the checks above keep passing while covering less. The
    # count is asserted against the policy document itself rather than against
    # a number retyped here, so the two cannot drift apart quietly.
    policy = _read("docs/release/versioning-policy.md")
    for _, relative, _ in DECLARATIONS:
        name = Path(relative).name
        assert name in policy, (
            f"{relative} is checked here but is not named in "
            f"versioning-policy.md. One of the two is out of date."
        )
    assert len(DECLARATIONS) == 8, (
        "This test covered eight declaration sites when written. If a product "
        "or platform was added, add its site above and to the policy document."
    )
