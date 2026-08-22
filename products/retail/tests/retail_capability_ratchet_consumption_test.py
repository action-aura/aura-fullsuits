"""
Aura Retail -- the guard on the guard: a capability ratchet must require the
verdict to be CONSUMED, not merely computed.

── THE DEFECT THIS FILE CLOSES ──────────────────────────────────────────────
retail_report_clock_money_disclosure_test.py carries a ratchet -- a handler
whose own SQL returns the shop's transacted money must carry a capability
check -- and it recognised the in-handler spelling by PRESENCE. Any
`session_has_capability` call anywhere in the handler counted, result unread.

An adversarial verifier defeated all nineteen tests in that file with one
dead line. Injected into api/retail_api.py:

    @retail_bp.route('/sales/weekly-takings', methods=['GET'])
    @mt_login_required
    @mt_require_subsystem('retail')
    def weekly_takings():
        _unused = session_has_capability(CAP_REPORTS)      # never read
        rows = conn.execute(
            "SELECT total, amount_paid FROM sales WHERE company_id=?", (_cid(),))
        return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

    -> 19 passed.

Delete the dead line, leave the ungated money route, and the ratchet correctly
went red:

    E   api/retail_api.py::weekly_takings returns ['amount_paid', 'total']

So the pass condition was "a call is present", not "a check ran" -- an
outcome-shaped guard inside the machinery built to prevent outcome-shaped
guards. `retail_capability_ratchet_ast.py` replaces presence with a data-flow
question ("does the verdict reach a branch, a return, or a raise?"), and this
file is what proves that replacement is real in BOTH directions:

  * it says NO to the defeating spelling -- and to six other ways of computing
    a verdict and then dropping it;
  * it still says YES to every spelling the production code actually uses,
    read off api/retail_api.py itself rather than off a paraphrase of it. A
    ratchet that started reporting real gates as absent would be swapped for
    an exemption list within a week, and an exemption list is an ungated route
    with extra steps.

── WHY THIS IS A SEPARATE FILE, AND WHY IT IS FAST ──────────────────────────
The module under test imports `ast` and nothing else, so this suite needs no
Flask app, no licence, no temp database -- which is the point. The guard is
now testable on its own terms, at the granularity of a single spelling,
instead of only being observable through a nineteen-test integration suite
that has to stand up a shop first.

The behavioural half -- that the check genuinely runs at request time, for a
real cashier, against a real route -- is asserted in
retail_route_capability_matrix_test.py's `_CapabilityProbe` tests. Neither
half replaces the other, and this file claims only the static half.

Run:
    pytest products/retail/tests/retail_capability_ratchet_consumption_test.py -v
"""
import ast
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PRODUCT_DIR = TESTS_DIR.parent
BACKEND_DIR = PRODUCT_DIR / 'backend'
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import retail_capability_ratchet_ast as ratchet  # noqa: E402

RETAIL_API = BACKEND_DIR / 'api' / 'retail_api.py'
IMPORT_API = BACKEND_DIR / 'api' / 'import_api.py'


def _handler(source):
    """The single function defined in a snippet, as an AST node."""
    tree = ast.parse(source)
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))


def _presence_only(fn):
    """THE OLD PREDICATE, reproduced verbatim in behaviour.

    Kept here rather than described, because every claim this file makes about
    being an improvement is a claim relative to it. If the two ever agreed on
    the defeating spelling, `test_the_new_analysis_is_strictly_stronger...`
    below fails and says so -- the improvement is asserted, not asserted-about.
    """
    for decorator in fn.decorator_list:
        if 'mt_require_capability' in ast.unparse(decorator):
            return f'decorator {ast.unparse(decorator)}'
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == 'session_has_capability'):
            return f'in-handler {ast.unparse(node)}'
    return None


# ─────────────────────────────────────────────────────────────────────────────
# THE DEFEATING SPELLING, first and on its own.
# ─────────────────────────────────────────────────────────────────────────────

#: Byte-for-byte the body the verifier injected, minus the Flask decorators
#: (which the analysis does not read for the in-handler question).
VERIFIER_DEAD_LINE = '''
def weekly_takings():
    _unused = session_has_capability(CAP_REPORTS)
    conn = get_retail_conn()
    rows = conn.execute(
        "SELECT total, amount_paid FROM sales WHERE company_id=?", (_cid(),)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})
'''


def test_the_verifier_dead_line_is_not_a_capability_check():
    """THE regression. This exact handler passed the ratchet as "gated"."""
    fn = _handler(VERIFIER_DEAD_LINE)
    kind, description = ratchet.capability_check_detail(fn)
    assert ratchet.capability_check_in(fn) is None, description
    assert kind == 'discarded', (kind, description)
    assert 'CAP_REPORTS' in description, (
        "the diagnosis does not even name the call it rejected -- a ratchet "
        "failure has to be readable by whoever has to fix it")


def test_the_new_analysis_is_strictly_stronger_than_the_presence_match():
    """Asserted as a PAIR, so it cannot rot into a tautology.

    The old predicate must still say "gated" for the defeating spelling -- if
    it did not, this file would be congratulating itself for fixing something
    that was never broken -- and the new one must say the opposite. The
    improvement is the DISAGREEMENT, and that is what is pinned."""
    fn = _handler(VERIFIER_DEAD_LINE)
    assert _presence_only(fn) is not None, (
        "the presence-matching predicate no longer accepts the dead line, so "
        "this file is no longer reproducing the defect it was written for")
    assert ratchet.capability_check_in(fn) is None
    assert _presence_only(fn) != ratchet.capability_check_in(fn)


# ─────────────────────────────────────────────────────────────────────────────
# THE FULL MATRIX OF SPELLINGS
#
# Both columns matter equally. The NOT-CONSUMED column is the ratchet; the
# CONSUMED column is what stops the ratchet being tightened into something
# that reports real gates as missing.
# ─────────────────────────────────────────────────────────────────────────────

CONSUMED_SPELLINGS = {
    # ── the four shapes api/retail_api.py actually uses ──────────────────────
    'direct in an if test': '''
def h():
    if not session_has_capability(CAP_DISCOUNT):
        return jsonify({'status': 'error'}), 403
''',
    'direct, through an and': '''
def h():
    if wants_discount and not session_has_capability(CAP_DISCOUNT):
        return jsonify({'status': 'error'}), 403
''',
    'direct, through a subscript comparison and an and': '''
def h():
    if p['party_type'] == 'supplier' and not session_has_capability(CAP_EMPLOYEES):
        conn.close()
        return jsonify({'status': 'error'}), 403
''',
    'bound once, then branched on (recent_sales)': '''
def h():
    may_read_the_book = session_has_capability(CAP_REPORTS)
    if not may_read_the_book:
        if date_from or date_to:
            return jsonify({'status': 'error'}), 403
        limit = min(limit, TILL_SALES_LOOKUP_MAX_LIMIT)
    return jsonify({'data': rows})
''',
    # ── other spellings a future gate could reasonably use ───────────────────
    'bound, then read in a ternary': '''
def h():
    ok = session_has_capability(CAP_REPORTS)
    payload = rows if ok else redact(rows)
    return jsonify({'data': payload})
''',
    'bound, then read far away in a second branch': '''
def h():
    ok = session_has_capability(CAP_REPORTS)
    conn = get_retail_conn()
    rows = conn.execute("SELECT total FROM sales").fetchall()
    conn.close()
    if ok:
        return jsonify({'data': rows})
    return jsonify({'data': []})
''',
    'walrus inside the if test': '''
def h():
    if not (ok := session_has_capability(CAP_REPORTS)):
        return jsonify({'status': 'error'}), 403
    return jsonify({'ok': ok})
''',
    'compared explicitly against False': '''
def h():
    if session_has_capability(CAP_REPORTS) == False:
        return jsonify({'status': 'error'}), 403
''',
    'the ternary test itself': '''
def h():
    limit = 50 if session_has_capability(CAP_REPORTS) else 10
    return jsonify({'limit': limit})
''',
    'returned directly as the verdict': '''
def h():
    return session_has_capability(CAP_REPORTS)
''',
    'consumed by a raise': '''
def h():
    raise Forbidden(session_has_capability(CAP_REPORTS))
''',
    'an assert': '''
def h():
    assert session_has_capability(CAP_REPORTS)
    return jsonify({'data': rows})
''',
    'a comprehension filter': '''
def h():
    return jsonify({'data': [r for r in rows if session_has_capability(CAP_REPORTS)]})
''',
    'inside a nested helper (limit 2: syntactic, not reachability)': '''
def h():
    def check():
        if not session_has_capability(CAP_REPORTS):
            abort(403)
    check()
    return jsonify({'data': rows})
''',
    'the dotted spelling': '''
def h():
    if not mt_auth.session_has_capability(CAP_REPORTS):
        return jsonify({'status': 'error'}), 403
''',
}

NOT_CONSUMED_SPELLINGS = {
    'the verifier dead line': VERIFIER_DEAD_LINE,
    'bare expression statement': '''
def h():
    session_has_capability(CAP_REPORTS)
    return jsonify({'data': rows})
''',
    'bare negated expression statement': '''
def h():
    not session_has_capability(CAP_REPORTS)
    return jsonify({'data': rows})
''',
    'bound, then only logged': '''
def h():
    ok = session_has_capability(CAP_REPORTS)
    logging.getLogger('aura.retail').info('caps=%s', ok)
    return jsonify({'data': rows})
''',
    'disclosed in the payload rather than acted on': '''
def h():
    return jsonify({'data': rows, 'may_read': session_has_capability(CAP_REPORTS)})
''',
    'bound, then disclosed in the payload': '''
def h():
    ok = session_has_capability(CAP_REPORTS)
    return jsonify({'data': rows, 'may_read': ok})
''',
    'appended to a list nobody branches on': '''
def h():
    out = []
    out.append(session_has_capability(CAP_REPORTS))
    return jsonify({'data': rows})
''',
    'interpolated into a string': '''
def h():
    return jsonify({'note': f'caps={session_has_capability(CAP_REPORTS)}'})
''',
    'rebound to a second name that is also never read': '''
def h():
    ok = session_has_capability(CAP_REPORTS)
    also = ok
    return jsonify({'data': rows})
''',
    # ── the shortest defeat of a bare "reaches a decision" rule ──────────────
    # Found by attacking this module's own first draft: the verdict genuinely
    # reaches an `if` test, and the `if` does nothing whatsoever.
    'an inert branch': '''
def h():
    if not session_has_capability(CAP_REPORTS):
        pass
    return jsonify({'data': rows})
''',
    'an inert branch via a name': '''
def h():
    ok = session_has_capability(CAP_REPORTS)
    if not ok:
        ...
    return jsonify({'data': rows})
''',
    'both arms inert': '''
def h():
    if session_has_capability(CAP_REPORTS):
        pass
    else:
        pass
    return jsonify({'data': rows})
''',
}


@pytest.mark.parametrize('label', sorted(CONSUMED_SPELLINGS))
def test_a_consumed_verdict_reads_as_a_real_gate(label):
    """False negatives are how a ratchet gets an exemption list bolted onto it
    until it means nothing. Every spelling here really does act on the
    verdict, so every one must be recognised."""
    fn = _handler(CONSUMED_SPELLINGS[label])
    kind, description = ratchet.capability_check_detail(fn)
    assert kind == 'in-handler', (label, kind, description)
    assert ratchet.capability_check_in(fn) is not None, label


@pytest.mark.parametrize('label', sorted(NOT_CONSUMED_SPELLINGS))
def test_a_dropped_verdict_does_not_read_as_a_gate(label):
    """The ratchet. Each of these computes the verdict and then fails to act
    on it, which gates precisely nothing."""
    fn = _handler(NOT_CONSUMED_SPELLINGS[label])
    kind, description = ratchet.capability_check_detail(fn)
    assert kind == 'discarded', (label, kind, description)
    assert ratchet.capability_check_in(fn) is None, (label, description)


def test_an_inert_branch_is_diagnosed_as_inert_and_not_merely_as_absent():
    """The residual attack on THIS module's first draft, named precisely.

    "Reaches a decision" was the rule; `if not session_has_capability(C):
    pass` satisfies it and gates nothing. The grade has to say WHICH defeat
    it is -- 'you computed a verdict and dropped it' and 'you branched on a
    verdict and the branch does nothing' get different fixes, and a guard
    that collapses them tells whoever hits it the wrong thing.

    Honest limit 7 records what is still open past this: a branch whose body
    is present but inconsequential (`_noted = True`) is not decidable
    statically, and the module says so instead of implying otherwise."""
    fn = _handler(NOT_CONSUMED_SPELLINGS['an inert branch'])
    graded = ratchet.graded_capability_calls(fn)
    assert graded == [('session_has_capability(CAP_REPORTS)', ratchet.UNCONSUMED_INERT)], graded
    assert ratchet.capability_check_in(fn) is None

    # the effectful twin of the SAME shape must still read as a real gate,
    # or this tightening has just broken every genuine in-handler check
    effectful = _handler('''
def h():
    if not session_has_capability(CAP_REPORTS):
        return jsonify({'status': 'error'}), 403
    return jsonify({'data': rows})
''')
    assert ratchet.graded_capability_calls(effectful) == [
        ('session_has_capability(CAP_REPORTS)', ratchet.CONSUMED_BRANCH)]


def test_the_matrix_is_a_real_partition_and_not_a_rubber_stamp_either_way():
    """Two ways this file could pass while proving nothing: an analysis that
    says YES to everything (the old defect) or NO to everything (a ratchet
    nobody can satisfy). Both columns being non-empty AND disjoint is the
    thing that rules out both at once."""
    yes = {label for label in CONSUMED_SPELLINGS
           if ratchet.capability_check_in(_handler(CONSUMED_SPELLINGS[label])) is not None}
    no = {label for label in NOT_CONSUMED_SPELLINGS
          if ratchet.capability_check_in(_handler(NOT_CONSUMED_SPELLINGS[label])) is None}
    assert len(yes) == len(CONSUMED_SPELLINGS) >= 10, sorted(set(CONSUMED_SPELLINGS) - yes)
    assert len(no) == len(NOT_CONSUMED_SPELLINGS) >= 8, sorted(set(NOT_CONSUMED_SPELLINGS) - no)


def test_a_handler_with_no_check_at_all_is_distinguished_from_a_dropped_one():
    """'there is no check here' and 'you computed a verdict and dropped it'
    are different bugs and get different diagnoses -- otherwise a ratchet
    failure tells whoever has to fix it nothing about what to do."""
    fn = _handler('''
def list_products():
    conn = get_retail_conn()
    rows = conn.execute("SELECT * FROM products WHERE company_id=?", (_cid(),)).fetchall()
    conn.close()
    return jsonify({'data': [dict(r) for r in rows]})
''')
    kind, description = ratchet.capability_check_detail(fn)
    assert kind is None and description is None, (kind, description)
    assert ratchet.capability_check_in(fn) is None


def test_the_decorator_form_is_still_recognised_and_wins():
    """`@mt_require_capability` cannot fail to consume its own verdict, so it
    is answered without any data-flow question -- including for a handler that
    ALSO happens to contain a dropped in-handler call."""
    fn = _handler('''
@retail_bp.route('/reports/summary', methods=['GET'])
@mt_login_required
@mt_require_capability(CAP_REPORTS)
def report_summary():
    _unused = session_has_capability(CAP_REPORTS)
    return jsonify({'data': rows})
''')
    kind, description = ratchet.capability_check_detail(fn)
    assert kind == 'decorator', (kind, description)
    assert description.startswith('decorator ')
    assert 'CAP_REPORTS' in description


# ─────────────────────────────────────────────────────────────────────────────
# AGAINST THE REAL FILE
#
# Everything above is synthetic. Synthetic snippets prove the analysis handles
# the shapes I THOUGHT OF; only the real file proves it handles the shapes the
# product actually ships. A wave was lost to a module that did not compile
# while its own guard read green, so the analysis is also pointed at the
# artefact itself.
# ─────────────────────────────────────────────────────────────────────────────

def _real_calls():
    """Every session_has_capability call in the two real route files, graded."""
    out = []
    for path in (RETAIL_API, IMPORT_API):
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for source, grade in ratchet.graded_capability_calls(fn):
                out.append((fn.name, source, grade))
    return out


def test_the_real_route_files_were_actually_parsed():
    """A scan that matched nothing passes every assertion below for the worst
    possible reason -- the same first guard the sweeps it feeds all have."""
    handlers = list(ratchet.route_handlers(RETAIL_API))
    assert len(handlers) >= 70, len(handlers)
    assert any(name == 'recent_sales' for name, _fn in handlers)


def test_every_in_handler_capability_check_the_product_ships_is_consumed():
    """THE false-negative guard, against the artefact rather than a paraphrase.

    Tightening presence into data flow is a LOOSENING of what counts as gated
    in exactly one direction and a tightening in every other, so the risk it
    introduces is reporting a real, working gate as absent. Every
    `session_has_capability` call in the shipped route files is a deliberate
    gate; if any one of them stops grading as consumed, the analysis has become
    wrong about production code and this fails before the ratchet can start
    manufacturing false alarms."""
    calls = _real_calls()
    assert len(calls) >= 5, (
        f"the analysis found almost no capability calls in the real route "
        f"files -- it is not reading what it thinks it is: {calls}")
    dropped = [(fn, source, grade) for fn, source, grade in calls
               if grade not in ratchet.CONSUMING_GRADES]
    assert not dropped, (
        "these REAL, shipped capability checks now grade as unconsumed. Either "
        "production grew a genuinely dead check, or the analysis is wrong:\n  "
        + "\n  ".join(f"{fn}: {source} -> {grade}" for fn, source, grade in dropped))


def test_the_two_real_consumption_shapes_are_both_present_in_production():
    """The analysis carries a direct path and a via-a-name path, and only the
    second is why `recent_sales` is gated at all. If production ever stopped
    using one of them, half this module would be dead code nothing exercises
    against the real file -- so that becomes visible here rather than silently
    rotting."""
    calls = _real_calls()
    by_handler = {}
    for fn, source, grade in calls:
        by_handler.setdefault(fn, []).append(grade)

    assert 'recent_sales' in by_handler, (
        "recent_sales no longer reads a capability at all -- the via-a-name "
        "path has nothing real left to prove itself against")
    assert all(g in ratchet.CONSUMING_GRADES for g in by_handler['recent_sales'])

    # …and the direct form, used by create_sale / update_customer /
    # create_purchase_order / void_payment.
    direct_users = {fn for fn, _s, _g in calls} - {'recent_sales'}
    assert len(direct_users) >= 3, sorted(direct_users)


def test_the_real_file_still_contains_ungated_handlers():
    """A analysis that answered "gated" for every handler in the file would
    pass the two tests above perfectly. This is the counterweight: the real
    file must still contain handlers the analysis reports as carrying nothing,
    or the ratchet downstream of it is a rubber stamp."""
    verdicts = {name: ratchet.capability_check_in(fn)
                for name, fn in ratchet.route_handlers(RETAIL_API)}
    ungated = sorted(name for name, capability in verdicts.items() if capability is None)
    assert len(ungated) >= 10, (
        f"every route in retail_api.py reads as gated, which is not true of "
        f"this product -- the analysis is saying yes to everything: {verdicts}")
    assert 'list_products' in ungated, (
        "the catalogue read a till depends on now reads as gated; the existing "
        "sweeps pin it as deliberately open, so the analysis disagrees with them")
