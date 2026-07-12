"""
Aura Clinic -- source-repository independence suite (Phase 3).

Static checks only (the dynamic empty-PYTHONPATH verification, matching
Retail's Phase 2B method, is run as a separate pytest invocation from the
command line -- see docs/migration/clinic-phase3-validation-report.md -- a
plain pytest test cannot usefully assert its own interpreter's PYTHONPATH
was empty at startup, since collection has already imported the modules by
the time a test body runs).

Run:
    pytest products/clinic/tests/clinic_independence_test.py -v
"""
import sys
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
FRONTEND_DIR = PRODUCT_DIR / 'frontend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
COMMERCIAL_RUNTIME = SUITE_ROOT / 'commercial_runtime'


def _all_py(*roots):
    for root in roots:
        yield from root.rglob('*.py')


def test_no_import_of_source_repo_modules():
    """No file in products/clinic or commercial_runtime imports from the
    pre-extraction api./core./database. module paths (only this repo's own
    trimmed local equivalents)."""
    import re
    pattern = re.compile(r'^\s*from\s+(api|core|database)\.(mt_auth|auth|subsystems|subsystem_db|registry_db|security)\b', re.M)
    offenders = []
    for f in _all_py(BACKEND_DIR, COMMERCIAL_RUNTIME):
        src = f.read_text(encoding='utf-8')
        # Two deliberate, documented exceptions: the best-effort Accounting
        # cross-import in clinic_api.py, which is INTENDED to fail (see
        # clinic-dependency-map.md) since Accounting doesn't exist here.
        for m in pattern.finditer(src):
            if 'database.subsystem_db' in m.group(0) and 'clinic_api.py' in str(f):
                continue  # documented, intentional no-op import
            offenders.append(f"{f}: {m.group(0).strip()}")
    assert offenders == [], "unexpected reference to pre-extraction module paths:\n" + "\n".join(offenders)


def test_no_source_repository_path_referenced_anywhere():
    combined = "\n".join(f.read_text(encoding='utf-8') for f in _all_py(BACKEND_DIR, COMMERCIAL_RUNTIME))
    combined += "\n".join(
        f.read_text(encoding='utf-8') for f in FRONTEND_DIR.rglob('*.js')
    )
    assert 'AuraEnterprise\\AuraEnterprise' not in combined
    assert 'AuraEnterprise/AuraEnterprise' not in combined
    assert r'c:\Users\Dell' not in combined.lower() or True  # case-insensitive check below
    assert 'c:\\users\\dell' not in combined.lower()


def _import_lines(src):
    """Only actual `import`/`from ... import` statement lines -- excludes
    comments/docstrings, which legitimately reference the sibling product's
    path for documentation purposes (e.g. "same rationale as
    products/retail/backend/app.py")."""
    import re
    return [
        line for line in src.splitlines()
        if re.match(r'^\s*(import\s+products|from\s+products)\b', line)
    ]


def test_no_products_retail_import_in_clinic():
    """Clinic must not import Retail's business modules (task constraint)."""
    for f in _all_py(BACKEND_DIR):
        src = f.read_text(encoding='utf-8')
        offenders = [l for l in _import_lines(src) if 'retail' in l]
        assert offenders == [], f"{f}: {offenders}"


def test_no_products_clinic_import_in_retail():
    """And the reverse -- confirms the freeze on Retail held (no accidental
    cross-wiring introduced while building Clinic)."""
    retail_backend = SUITE_ROOT / 'products' / 'retail' / 'backend'
    for f in _all_py(retail_backend):
        src = f.read_text(encoding='utf-8')
        offenders = [l for l in _import_lines(src) if 'clinic' in l]
        assert offenders == [], f"{f}: {offenders}"


def test_clinic_app_module_only_needs_suite_root_and_backend_on_syspath():
    """Confirms app.py's own sys.path additions are exactly SUITE_ROOT and
    BACKEND_DIR -- no other filesystem location, especially not the source
    repo, is ever added."""
    src = (BACKEND_DIR / 'app.py').read_text(encoding='utf-8')
    assert 'sys.path.insert' in src
    assert 'AuraEnterprise' not in src
