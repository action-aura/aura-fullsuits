"""
Aura Retail -- the e-invoicing firewall for per-document-type numbering
series (schema v34). THE CONSTRAINT THAT DOMINATES THIS FEATURE: Jordan
e-invoicing's own dedicated sequence (`commercial_runtime/einvoicing/
sequence.py`) must NEVER share a counter, a table or a call graph with a
local document number -- docs/einvoicing/phase1/invoice-numbering-audit.md,
"Decision: option 2".

B1's fix, in full. The design this feature implements was reviewed once,
and the review found the ORIGINAL firewall test factually false: it
claimed `allocate_einvoice_number` has exactly ONE call site in the whole
repo, when there are actually TWO tracked production callers --
`products/retail/backend/core/retail/einvoice_adapter.py` AND
`products/clinic/backend/core/clinic/einvoice_adapter.py` -- plus 17
references inside `commercial_runtime/einvoicing/tests/test_sequence.py`.
Written as the original design specified, this file would have been RED
before this feature existed. The obvious repair -- narrow the scan to
`products/retail/` -- would have made the guard permanently blind to the
ONE thing it exists to see: a SECOND retail caller. So the scan here stays
REPO-WIDE, with clinic's own adapter and the sequence unit test file both
named as EXPECTED entries, and it is green on day one.

EVERY file-enumerating assertion below walks `git ls-files`, NEVER
`Path.rglob` or `os.walk`. `android/aura-retail/app/build/python/sources/
*/` and `android/aura-*/app/build/staged-python/` hold full, UNTRACKED
Chaquopy copies of `commercial_runtime/` and `products/` left over from a
previous build -- a filesystem walk would see every one of them and
double- (or sextuple-) count every real caller, and `git ls-files` shows
exactly the three real tracked call sites (verified directly against this
worktree while writing this file). `test_the_git_harness_itself_is_
what_prevents_double_counting` mutation-proves this discipline, per
ENGINEERING.md's "mutation-prove the harness too" (a thread-survival check
that is unreachable exactly when the guard is broken is the named failure
shape this repo has already been burned by once).

Run:
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \
        products/retail/tests/retail_doc_series_einvoice_firewall_test.py -v
"""
import ast
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DOC_SERIES_PY = BACKEND_DIR / 'core' / 'retail' / 'doc_series.py'


# ── The git-ls-files discipline itself ──────────────────────────────────────

def _git_tracked_py_files():
    """Every `.py` file `git` actually tracks in this repo -- NEVER a
    filesystem walk. See this file's own module docstring for why: the
    Android Chaquopy build trees hold full untracked copies of
    `commercial_runtime/` and `products/` that a `Path.rglob('*.py')`
    would see and a `git ls-files` call cannot."""
    out = subprocess.run(
        ['git', 'ls-files', '--', '*.py'],
        cwd=str(SUITE_ROOT), capture_output=True, text=True, check=True,
    )
    return sorted(p.strip() for p in out.stdout.splitlines() if p.strip())


def _call_site_files(rel_paths, symbol):
    """Which of these repo-relative tracked files contain a CALL to
    `symbol` (an `ast.Call` whose func's final attribute/name segment is
    `symbol`) -- not merely a textual mention, so a comment referencing the
    function by name (einvoice_adapter.py's own module docstring does
    exactly this) is never mistaken for a call site."""
    hits = set()
    for rel in rel_paths:
        path = SUITE_ROOT / rel
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        if symbol not in text:
            continue  # cheap pre-filter before paying for a real parse
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else None)
            if name == symbol:
                hits.add(rel.replace('\\', '/'))
                break
    return hits


def test_android_build_tree_copies_are_invisible_to_the_git_harness(tmp_path):
    """Half of the harness mutation proof, kept as PERMANENT, side-effect-
    free automated test code: a fake caller planted inside an UNTRACKED
    Android build directory must be INVISIBLE to `git ls-files` -- if this
    ever fails, this worktree's `.gitignore`/tracking has changed and every
    other assertion in this file that trusts `_git_tracked_py_files()` is
    now standing on a false premise.

    The OTHER half of this proof -- staging a fake caller into a genuinely
    TRACKED path and confirming the scan WOULD catch it -- is deliberately
    NOT embedded here: it requires mutating this worktree's git INDEX
    (`git add --intent-to-add` / `git reset`), which is exactly the kind
    of stateful side effect a routine automated test run should never
    perform. Run once by hand while writing this file instead (this
    session's own report quotes both directions verbatim), matching
    `retail_site_relay_schema_test.py`'s own documented convention for a
    mutation proof that is confirmed by hand rather than encoded as
    running test code."""
    fake_untracked = SUITE_ROOT / 'android' / 'aura-retail' / 'app' / 'build' / \
        'python' / 'sources' / 'debug' / '_firewall_test_fake_caller.py'
    fake_untracked.parent.mkdir(parents=True, exist_ok=True)
    fake_untracked.write_text("from commercial_runtime.einvoicing import sequence\n"
                               "sequence.allocate_einvoice_number(None, 1, 'income')\n",
                               encoding='utf-8')
    try:
        tracked = set(_git_tracked_py_files())
        rel = str(fake_untracked.relative_to(SUITE_ROOT)).replace('\\', '/')
        assert rel not in tracked, (
            "a file inside the Android Chaquopy build tree must never be tracked -- if it is, "
            "this worktree's .gitignore has changed and this whole test file's premise is stale")
    finally:
        fake_untracked.unlink(missing_ok=True)


# ── Tripwires (cheap, NOT proofs -- see the live test below for the one
#    assertion that actually proves a property) ─────────────────────────────

def test_doc_series_module_never_imports_or_mentions_einvoicing():
    """Passes on an empty file, and on any file that simply never mentions
    e-invoicing -- a tripwire, not a proof. `core/retail/doc_series.py`
    must import NOTHING from `commercial_runtime.einvoicing` and must not
    even mention 'einvoice'/'istd'/'jofotara' in any form (case-
    insensitive), including in comments. MUT: add
    `from commercial_runtime.einvoicing import sequence` -> RED."""
    text = DOC_SERIES_PY.read_text(encoding='utf-8')
    tree = ast.parse(text, filename=str(DOC_SERIES_PY))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert 'einvoic' not in alias.name.lower(), \
                    f"doc_series.py imports {alias.name!r} -- the firewall is broken"
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ''
            assert 'einvoic' not in mod.lower(), \
                f"doc_series.py imports from {mod!r} -- the firewall is broken"
    lowered = text.lower()
    for forbidden in ('einvoice', 'istd', 'jofotara'):
        assert forbidden not in lowered, \
            f"doc_series.py mentions {forbidden!r} -- see this feature's own e-invoicing firewall rule"


def test_einvoicing_package_never_mentions_doc_series():
    """The other direction of the same tripwire: every TRACKED module
    under `commercial_runtime/einvoicing/` must carry zero references to
    `doc_series`. MUT: add one -> RED."""
    tracked = [p for p in _git_tracked_py_files() if p.replace('\\', '/').startswith('commercial_runtime/einvoicing/')]
    assert tracked, "the tracked-file scan found nothing under commercial_runtime/einvoicing/ -- the scan itself is broken"
    offenders = []
    for rel in tracked:
        text = (SUITE_ROOT / rel).read_text(encoding='utf-8')
        if 'doc_series' in text.lower():
            offenders.append(rel)
    assert not offenders, f"commercial_runtime/einvoicing files reference doc_series: {offenders}"


# ── B1's fix: the respecified call-site scan ────────────────────────────────

#: The EXPECTED, repo-wide set of tracked files that call
#: `allocate_einvoice_number` -- named explicitly rather than inferred, so
#: this scan stays green on day one (retail's own caller is UNCHANGED by
#: this feature) and a NEW caller anywhere, in EITHER product, fails loudly
#: rather than silently expanding the allowlist.
EXPECTED_EINVOICE_NUMBER_CALLERS = {
    'products/retail/backend/core/retail/einvoice_adapter.py',
    'products/clinic/backend/core/clinic/einvoice_adapter.py',
    'commercial_runtime/einvoicing/tests/test_sequence.py',
}


def test_allocate_einvoice_number_has_exactly_the_expected_repo_wide_callers():
    """B1's respecification. The submitted design's own version claimed
    'exactly ONE call site in the repo', which is FALSE (two production
    callers, retail and clinic, plus the sequence unit test) and would have
    been red before this feature existed. MUT: add a second caller
    anywhere under `products/retail/` -> RED (extra entry not in the
    expected set). MUT: remove clinic's expected entry from this set ->
    RED (proving the allowlist is load-bearing, not decorative -- the
    real file still calls it, so the actual scan result no longer equals
    the (now-wrong) expected set)."""
    tracked = _git_tracked_py_files()
    actual = _call_site_files(tracked, 'allocate_einvoice_number')
    assert actual == EXPECTED_EINVOICE_NUMBER_CALLERS, (
        f"only in code: {sorted(actual - EXPECTED_EINVOICE_NUMBER_CALLERS)}\n"
        f"only in expected: {sorted(EXPECTED_EINVOICE_NUMBER_CALLERS - actual)}"
    )


def test_exactly_one_call_site_under_products_retail():
    """The half B1 actually needs for THIS feature: under `products/retail/`
    specifically, there is exactly one call site, and it is the einvoice
    adapter -- never `core/retail/doc_series.py` or any route in
    `retail_api.py`. MUT: add a second caller anywhere under
    products/retail/ -> RED."""
    tracked = [p for p in _git_tracked_py_files() if p.replace('\\', '/').startswith('products/retail/')]
    actual = _call_site_files(tracked, 'allocate_einvoice_number')
    assert actual == {'products/retail/backend/core/retail/einvoice_adapter.py'}, actual


# ── Reserved local prefixes: derived, and derived LIVE ──────────────────────

def test_reserved_prefix_values_are_refused_when_creating_a_series():
    from commercial_runtime.einvoicing import sequence
    assert 'INC' in sequence.RESERVED_LOCAL_PREFIXES
    assert 'GS' in sequence.RESERVED_LOCAL_PREFIXES


def test_reserved_prefixes_are_derived_live_not_hardcoded(monkeypatch):
    """Proves the DERIVATION, not just today's two values -- the defect a
    hardcoded `frozenset({'INC', 'GS'})` in the route would reintroduce.
    `commercial_runtime/einvoicing/sequence.py`'s `RESERVED_LOCAL_PREFIXES`
    is implemented via module-level `__getattr__` (PEP 562) specifically so
    THIS monkeypatch -- applied AFTER the module is already imported --
    is visible on the very next access. MUT: hardcode
    `frozenset({'INC', 'GS'})` in the route instead of importing this name
    -> RED (a book coded 'XY' would be accepted)."""
    from commercial_runtime.einvoicing import sequence
    monkeypatch.setitem(sequence._PREFIX, 'thirdfamily', 'XY')
    assert 'XY' in sequence.RESERVED_LOCAL_PREFIXES


def test_create_doc_series_route_rejects_a_newly_reserved_prefix(monkeypatch):
    """The end-to-end proof of the live derivation, through the REAL
    route, not just the module function."""
    from commercial_runtime.einvoicing import sequence
    monkeypatch.setitem(sequence._PREFIX, 'thirdfamily', 'XY')

    client, _cid = _make_admin_client()
    r = client.post('/api/sub/retail/doc-series', json={'doc_type': 'sale', 'code': 'XY', 'label': 'x'})
    assert r.status_code == 400, r.get_json()


# ── THE LIVE ONE -- the only assertion in this file that PROVES a property
#    rather than merely tripwiring an absence ────────────────────────────────

_APP = None


def _app():
    global _APP
    if _APP is not None:
        return _APP
    data = Path(tempfile.mkdtemp(prefix="aura_retail_doc_series_firewall_"))
    (data / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
    os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(data))
    os.environ.pop("AURA_DEV", None)
    os.environ["AURA_EINVOICING_ALLOW_MOCK"] = "1"

    from commercial_runtime.licensing_contracts.test_support import seed_active_license
    seed_active_license(str(data), product_code="AURA_RETAIL", platform="WINDOWS")
    os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

    import app as _app_module
    flask_app = _app_module.init_app()
    flask_app.config["TESTING"] = True

    def _cleanup():
        for worker in _app_module._einvoicing_workers.values():
            worker.stop()
        shutil.rmtree(data, ignore_errors=True)

    import atexit
    atexit.register(_cleanup)
    _APP = flask_app
    return flask_app


def _make_admin_client():
    # `_app()` FIRST, deliberately -- it is what sets AURA_APP_DATA before
    # ANYTHING under commercial_runtime is ever imported. `registry_db.py`/
    # `mt_auth.py` both resolve their own database path from that env var
    # at THEIR OWN import time (AUDIT-010's exact warning, restated here
    # because this file tripped over it while being written: the first
    # draft imported `registry_db` at the top of this function, BEFORE
    # calling `_app()`, which froze it to the fallback default path --
    # nothing under commercial_runtime/ may be imported before `_app()`
    # has run at least once in this process).
    flask_app = _app()
    from commercial_runtime.identity.registry_db import get_conn as registry_conn
    from commercial_runtime.security.passwords import hash_password
    email = f"firewall-{uuid.uuid4().hex[:10]}@test.local"
    password = "FirewallPW1"
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()
    client = flask_app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    return client, company_id


def test_a_series_and_an_einvoice_both_mint_independently_on_one_real_sale():
    """THE LIVE ONE. Configures a `doc_series` 'sale' book AND enables
    e-invoicing (MockProvider) on the SAME company, rings three real sales,
    and proves BOTH sequences independently:

      * `sales.sale_number` is the doc_series book's own `A-000001..3`
      * `einvoice_outbox.einvoice_no` is the UNTOUCHED ISTD sequence,
        `INC-000001..3`
      * `local_document_no` (what the ISTD document carries as a
        cross-reference) equals the SERIES number, and is never equal to
        `einvoice_no`.

    This is the only assertion in this file that PROVES the firewall
    holds under real, mixed use rather than merely tripwiring an absence.
    MUT-1: point `doc_series.allocate` at `einvoice_sequence` instead of
    `doc_series_counter` -> the ISTD numbers skip (INC-000002/000004/...)
    -> RED. MUT-2: change the series' `pad_width`/`start_no` -> `einvoice_
    no` must stay BYTE-IDENTICAL regardless -> proves series configuration
    cannot reach the submitted sequence at all -> RED if it did.
    """
    # `_make_admin_client()` (which calls `_app()`) FIRST -- see that
    # function's own comment on why nothing under commercial_runtime/ or
    # database/ may be imported before AURA_APP_DATA is set for this
    # process (AUDIT-010).
    client, cid = _make_admin_client()
    from api import retail_api
    from database.schema import get_retail_conn

    client.post('/api/einvoicing/settings', json={'enabled': '1', 'invoice_family': 'income'})
    series = client.post('/api/sub/retail/doc-series',
                          json={'doc_type': 'sale', 'code': 'A', 'label': 'Main', 'start_no': 500})
    assert series.status_code == 200, series.get_json()
    series_id = series.get_json()['data']['id']

    terminal_id = 'firewall-till-' + uuid.uuid4().hex[:8]
    previous = retail_api.local_terminal_id
    retail_api.local_terminal_id = lambda: terminal_id
    try:
        claim = client.post(f'/api/sub/retail/doc-series/{series_id}/allocator-claim')
        assert claim.status_code == 200, claim.get_json()

        pid_resp = client.post('/api/sub/retail/products', json={
            'name': 'Firewall Widget', 'sku': f'FW-{uuid.uuid4().hex[:8]}',
            'cost_price': 5.0, 'sell_price': 20.0, 'tax_rate': 10.0, 'initial_stock': 100,
        })
        assert pid_resp.status_code == 200, pid_resp.get_json()
        pid = pid_resp.get_json()['data']['id']

        sale_ids = []
        sale_numbers = []
        for _ in range(3):
            r = client.post('/api/sub/retail/sales', json={
                'items': [{'product_id': pid, 'quantity': 1}],
                'amount_paid': 999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
            })
            assert r.status_code == 200, r.get_json()
            sale_ids.append(r.get_json()['data']['id'])
            sale_numbers.append(r.get_json()['data']['sale_number'])
    finally:
        retail_api.local_terminal_id = previous

    assert sale_numbers == ['A-000500', 'A-000501', 'A-000502'], sale_numbers

    conn = get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT source_id, local_document_no, einvoice_no FROM einvoice_outbox "
            "WHERE company_id=? AND source_type='sale' ORDER BY id", (cid,),
        ).fetchall()
    finally:
        conn.close()
    assert [r['source_id'] for r in rows] == sale_ids, "an outbox row is missing for one of the three sales"
    einvoice_numbers = [r['einvoice_no'] for r in rows]
    assert einvoice_numbers == ['INC-000001', 'INC-000002', 'INC-000003'], (
        f"the ISTD sequence must be gapless and UNAFFECTED by the doc_series book's own "
        f"start_no=500 -- got {einvoice_numbers!r}")
    for row, expected_local in zip(rows, sale_numbers):
        assert row['local_document_no'] == expected_local
        assert row['local_document_no'] != row['einvoice_no'], (
            "local_document_no and einvoice_no collided -- the two sequences are not independent")
