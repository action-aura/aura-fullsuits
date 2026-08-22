"""
Aura Retail -- "was a capability actually CONSULTED?", answered by data flow
rather than by the presence of a call.

(FILENAME. This is a helper MODULE, not a suite: `run_all_tests.py::discover`
globs `*_test.py` / `test_*.py`, so this file is imported, never collected.
The `retail_capability_ratchet_` prefix is this wave's file-OWNERSHIP
convention. Its own suite is retail_capability_ratchet_consumption_test.py,
and it is deliberately import-light -- `ast` and nothing else -- so that suite
can exercise it without standing up a Flask app, a licence and a temp
database first.)

── WHY THIS EXISTS ──────────────────────────────────────────────────────────
`retail_report_clock_money_disclosure_test.py` carries a ratchet: a handler
whose own SQL returns the shop's transacted money must carry a capability
check. It recognised two spellings of "gated" -- the `@mt_require_capability`
decorator, and the in-handler `session_has_capability(CODE)` form that exists
for "the checks a decorator cannot express".

The in-handler half was recognised by PRESENCE. Any `session_has_capability`
call anywhere in the handler counted, with its result unread. An adversarial
verifier defeated the whole ratchet with one dead line:

    @retail_bp.route('/sales/weekly-takings', methods=['GET'])
    @mt_login_required
    @mt_require_subsystem('retail')
    def weekly_takings():
        _unused = session_has_capability(CAP_REPORTS)     # <- never read
        rows = conn.execute("SELECT total, amount_paid FROM sales ...")
        return jsonify(...)

All nineteen tests stayed green. Delete the dead line and the ratchet
correctly went red -- so the pass condition was "a call is present", not "a
check ran". That is an outcome-shaped guard wearing the costume of a
mechanism-shaped one, in the very machinery built to stop exactly that.

── WHAT THIS MODULE ASKS INSTEAD ────────────────────────────────────────────
Not "does the name appear?" -- the last three source-text guards in this repo
were each defeated by a spelling (an interpolated table name, a
variable-assembled DROP, and this one). The question here is a data-flow one,
asked of the AST:

    does the VALUE returned by session_has_capability(...) reach a
    decision -- the test of a branch, the value of a return, or a raise?

A verdict that is computed and then dropped on the floor has not gated
anything, and the analysis says so.

Two ways a value gets there, both of which the real code uses:

  DIRECT      `if wants_discount and not session_has_capability(CAP_DISCOUNT):`
              the call sits inside the `if` test, reached through
              value-preserving wrappers (`not`, `and`, `==`).

  VIA A NAME  `may_read_the_book = session_has_capability(CAP_REPORTS)`
              `if not may_read_the_book:`
              the call is bound, and a later READ of that binding reaches a
              decision. `recent_sales` is written this way on purpose (it
              reads once so two calls cannot straddle a permission change),
              so an analysis that only understood the direct form would
              report the one route this machinery was built for as ungated.

── HONEST LIMITS (read before trusting a green) ─────────────────────────────
Written out rather than glossed, because a previous guard in this area
claimed coverage it did not have and the CLAIM is what let the bug through.

1. NOT INTERPROCEDURAL. `abort_unless(session_has_capability(CODE))` -- a
   helper that raises -- is a real gate this module scores as `packaged`,
   i.e. NOT consumed. It cannot see inside `abort_unless`. This is the
   fail-closed direction for a ratchet (a false ALARM, never a false pass:
   a genuinely-gated route gets flagged and a human adjudicates it), but it
   is a limit, and if that idiom ever arrives here this module needs
   widening rather than an exemption bolted onto the ratchet.

2. NOT REACHABILITY. A call inside a nested function or a branch that never
   executes still scores as consumed if the value syntactically reaches a
   decision. This module proves the value is USED, not that the code RUNS.
   The behavioural half of that -- that the check is genuinely invoked at
   request time -- is what the live `_CapabilityProbe` tests in
   retail_route_capability_matrix_test.py assert, and neither half replaces
   the other.

3. NOT VALUE-SENSITIVE. It cannot tell `if not session_has_capability(C):
   return 403` from `if session_has_capability(C): return 403` -- both
   consume the verdict. Whether the gate points the right way is a
   behavioural question and is asserted live, per role, elsewhere.

4. NAME-SHADOWING. Via-a-name tracking is by identifier over the whole
   handler, with no scope or ordering analysis: binding the verdict to a
   name that some unrelated line happens to read scores as consumed.

   CORRECTION, 2026-08-22. This limit previously said the shapes it misses
   are "contrived rather than accidental". That was wrong, and the claim was
   load-bearing, so it is withdrawn rather than softened. A verifier defeated
   the analysis with ONE line using the commonest name in a list handler:

       limit = session_has_capability(CAP_REPORTS)   # verdict dropped
       limit = int(request.args.get('limit', 50))    # the handler's own line
       if limit > 500: limit = 500                   # launders it to "branch"

   Same cost as the defeat this module closed. `limit`, `ok`, `page` and `r`
   are ordinary names, not contrivances. Fixing it properly needs real scope
   and ordering analysis, which is not worth building here -- see the note
   below on what now carries this property instead.

5. TUPLE UNPACKING. `a, b = session_has_capability(C), 1` scores as
   `packaged`, because the value is tracked to the tuple and not through it.
   Strict, i.e. fail-closed, and rare enough not to be worth positional
   matching.

6. `assert session_has_capability(C)` scores as consumed, but Python strips
   asserts under `-O`. This module grades source, not deployment.

7. THE RESIDUAL, stated plainly rather than left for the next verifier to
   find. "Reaches a decision" is not "changes the outcome". The shortest
   remaining defeat is an inert branch --

       if not session_has_capability(CAP_REPORTS):
           pass

   -- and THAT ONE is closed (grade `inert-branch`, see
   `_branch_has_an_effect`). But the next one up is not:

       if not session_has_capability(CAP_REPORTS):
           _noted = True          # a branch with a body that does nothing

   No static analysis short of "does this affect the response" closes that,
   and "does this affect the response" is not a decidable question about a
   Flask handler.

── WHAT THIS MODULE'S CLAIM WAS REDUCED TO, AND WHY ─────────────────────────
It previously claimed to make "accidental ungating impossible". That claim is
WITHDRAWN. A verifier defeated the surrounding ratchet three separate ways
against a booted app, each time shipping a live route that returned
`SUM(total) = 400.00` to a real cashier while four static sweeps stayed green:

  * SQL hoisted to a module-level constant -- `_handler_sql` walks only the
    handler subtree, and `retail_api.py` already uses that idiom elsewhere;
  * an interpolated table or column name -- `f"SELECT {cols} FROM sales"` --
    which is the SAME defeat that beat the three source-text guards before it;
  * limit 4 above, one dead assignment to an ordinary name.

Three rounds of hardening, three defeats, each by a different spelling. That
is not bad luck: proving "this value influenced authorisation" over arbitrary
Python is undecidable, and every round spent on it was a round not spent on
the product.

So the property is now carried BEHAVIOURALLY, by
`retail_money_leak_runtime_sweep_test.py`, which boots the app, calls every
sweepable route as a real cashier, and reads the RESPONSE. That cannot be
defeated by a spelling, because an f-string, a module constant, a hoisted
helper and a literal all produce identical HTTP output -- and the output is
what a customer actually receives. It catches all three defeats above; the
module-level-constant injection was re-run against it and flagged immediately.

THIS MODULE IS KEPT, with a smaller job: it is fast, needs no Flask app, and
catches the careless cases early, in the editor, where the runtime sweep is
too slow to live. It is a linter, not a proof. Where the two disagree, the
runtime sweep is right.

   It is NOT a proof that a route is authorised. That claim belongs to the
   live per-role tests in retail_route_capability_matrix_test.py and
   retail_report_clock_money_disclosure_test.py §1, which drive real
   sessions against real routes. This module is the thing that stops a NEW
   route escaping those tests unnoticed; it is not a substitute for them.
"""
import ast

#: The one in-handler capability reader this codebase has. Both the bare name
#: (`from ...mt_auth import session_has_capability`, which is how retail_api.py
#: writes it) and any dotted spelling (`mt_auth.session_has_capability`) are
#: recognised -- accepting a spelling nobody uses yet costs nothing, and
#: missing one is how a real gate gets reported as absent.
CAPABILITY_READER = 'session_has_capability'

# ── grades ───────────────────────────────────────────────────────────────────
# The first three mean the verdict reached a decision. The last two mean it did
# not, and are kept DISTINCT rather than collapsed to None so a ratchet failure
# can say WHICH way the check was defeated -- "you computed it and dropped it"
# is a completely different conversation from "there is no check here at all".
CONSUMED_BRANCH = 'branch'
CONSUMED_RETURN = 'return'
CONSUMED_RAISE = 'raise'
UNCONSUMED_DISCARDED = 'discarded'
UNCONSUMED_PACKAGED = 'packaged'
#: The verdict reached a branch, and the branch does NOTHING -- every arm is
#: `pass` / `...` / a bare string. `if not session_has_capability(C): pass` is
#: the shortest defeat of a reaches-a-decision rule, and it gates exactly as
#: much as the dead line did. See honest limit 7 for what remains open past it.
UNCONSUMED_INERT = 'inert-branch'

CONSUMING_GRADES = frozenset({CONSUMED_BRANCH, CONSUMED_RETURN, CONSUMED_RAISE})
NON_CONSUMING_GRADES = frozenset({UNCONSUMED_DISCARDED, UNCONSUMED_PACKAGED, UNCONSUMED_INERT})

#: Nodes that hand a value onward UNCHANGED in the sense that matters here:
#: whatever the enclosing expression is worth is still derived from the
#: capability verdict, so climbing past one does not lose the thread.
#: `not x`, `a and x`, `x == False`, `*x`, `await x`.
_VALUE_PRESERVING = (ast.BoolOp, ast.UnaryOp, ast.Compare, ast.Starred, ast.Await)


def parent_map(root):
    """child node -> its parent, for one handler's subtree.

    The stdlib `ast` deliberately does not record parents, and the whole
    question this module asks ("where does this value GO?") is an upward one.
    """
    parents = {}
    for node in ast.walk(root):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def is_capability_call(node):
    """`session_has_capability(...)` in either the bare or the dotted spelling."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == CAPABILITY_READER
    if isinstance(func, ast.Attribute):
        return func.attr == CAPABILITY_READER
    return False


def _branch_has_an_effect(node):
    """Does this `if`/`while` actually DO anything on either arm?

    Trivial means `pass`, `...`, or a bare string/constant expression -- the
    statements that exist to satisfy the grammar. A branch built entirely from
    those is a decision with no consequence, which is the same amount of
    gating the verifier's dead line achieved.
    """
    def trivial(statement):
        if isinstance(statement, ast.Pass):
            return True
        return isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)

    return not all(trivial(s) for s in list(node.body) + list(node.orelse))


def _assigned_names(node):
    """Every identifier a binding statement writes to."""
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [sub.id for target in targets for sub in ast.walk(target)
            if isinstance(sub, ast.Name)]


def grade_value(node, root, parents=None, _seen=None):
    """How the value produced by `node` is used, as one of the five grades.

    Walks UP from the node. At each step the only question is what role the
    value plays in its parent: a decision (stop, consumed), a binding (switch
    to following the name), a wrapper (keep climbing, same value), or anything
    else (stop, not consumed).
    """
    if parents is None:
        parents = parent_map(root)
    if _seen is None:
        _seen = set()

    child = node
    while True:
        parent = parents.get(child)

        # Fell off the top of the handler without meeting a decision. Reached
        # by e.g. a docstring-level expression; treat as dropped.
        if parent is None:
            return UNCONSUMED_DISCARDED

        # ── the value is thrown away outright ────────────────────────────────
        # A bare expression statement. `_unused = shc(C)` does NOT land here --
        # that is an Assign, handled below -- but `shc(C)` on a line by itself
        # does, and so does `not shc(C)` on a line by itself.
        if isinstance(parent, ast.Expr):
            return UNCONSUMED_DISCARDED

        # ── the value IS a decision ─────────────────────────────────────────
        if isinstance(parent, (ast.If, ast.While)) and parent.test is child:
            # …but a branch that does nothing has not gated anything. Checked
            # only for statement branches: an IfExp always yields a value and
            # an assert always has an effect, so neither can be inert.
            return CONSUMED_BRANCH if _branch_has_an_effect(parent) else UNCONSUMED_INERT
        if isinstance(parent, (ast.IfExp, ast.Assert)) and parent.test is child:
            return CONSUMED_BRANCH
        if isinstance(parent, ast.Match) and parent.subject is child:
            return CONSUMED_BRANCH
        if isinstance(parent, ast.comprehension) and any(t is child for t in parent.ifs):
            return CONSUMED_BRANCH
        if isinstance(parent, ast.Return) and parent.value is child:
            return CONSUMED_RETURN
        if isinstance(parent, ast.Raise):
            return CONSUMED_RAISE
        # `raise Forbidden(session_has_capability(C))` -- the verdict is
        # consumed by the raise, one construction call away. Narrow on purpose:
        # a Call is otherwise opaque (limit 1), and widening it generally would
        # let `jsonify({'may': shc(C)})` score as a gate.
        if isinstance(parent, ast.Call) and isinstance(parents.get(parent), ast.Raise):
            return CONSUMED_RAISE

        # ── bound to a name: follow every READ of that name ──────────────────
        if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and parent.value is child:
            return _grade_names(_assigned_names(parent), root, parents, _seen)
        if isinstance(parent, ast.NamedExpr) and parent.value is child:
            # A walrus both binds AND yields, so try the binding first and
            # otherwise keep climbing with the inline value.
            via_name = _grade_names([parent.target.id], root, parents, _seen)
            if via_name in CONSUMING_GRADES:
                return via_name
            child = parent
            continue

        # ── value-preserving wrappers: same verdict, keep climbing ───────────
        if isinstance(parent, _VALUE_PRESERVING):
            child = parent
            continue
        if isinstance(parent, ast.IfExp):
            # Not the test (handled above), so this is the body or the orelse:
            # the IfExp's value is the verdict.
            child = parent
            continue

        # ── anything else PACKAGES the value ────────────────────────────────
        # A call argument, a dict/list/set/tuple, an f-string, a subscript, an
        # attribute access. The value went somewhere this module cannot follow,
        # so it does not count as a gate. See honest limit 1.
        return UNCONSUMED_PACKAGED


def _grade_names(names, root, parents, seen):
    """Best grade reachable from any READ of any of these identifiers.

    `seen` breaks the `a = b; b = a` cycle and stops one name being chased
    twice within a single grading.
    """
    best = UNCONSUMED_DISCARDED
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        for node in ast.walk(root):
            if (isinstance(node, ast.Name) and node.id == name
                    and isinstance(node.ctx, ast.Load)):
                grade = grade_value(node, root, parents, seen)
                if grade in CONSUMING_GRADES:
                    return grade
                if grade == UNCONSUMED_PACKAGED:
                    # Read, but into somewhere unfollowable -- still better
                    # evidence than "never read at all", and the distinction
                    # is what makes a ratchet failure diagnosable.
                    best = UNCONSUMED_PACKAGED
    return best


def graded_capability_calls(fn):
    """[(source of each session_has_capability call, its grade)], in source order."""
    parents = parent_map(fn)
    return [(ast.unparse(node), grade_value(node, fn, parents))
            for node in ast.walk(fn) if is_capability_call(node)]


def decorator_capability(fn):
    """The `@mt_require_capability(...)` decorator's source, if the handler
    carries one. Whole-route form -- no data-flow question arises, the
    decorator cannot fail to consume its own verdict."""
    for decorator in fn.decorator_list:
        text = ast.unparse(decorator)
        if 'mt_require_capability' in text:
            return text
    return None


def capability_check_detail(fn):
    """(kind, description) for one route handler.

    kind is one of:
      'decorator'   @mt_require_capability(...)
      'in-handler'  a session_has_capability(...) whose verdict reaches a
                    decision -- a real gate
      'discarded'   a session_has_capability(...) whose verdict is computed
                    and then never acted on -- NOT a gate, and the exact shape
                    the verifier used to defeat the ratchet
      None          no capability check of any kind
    """
    decorator = decorator_capability(fn)
    if decorator is not None:
        return 'decorator', f'decorator {decorator}'

    graded = graded_capability_calls(fn)
    for source, grade in graded:
        if grade in CONSUMING_GRADES:
            return 'in-handler', f'in-handler {source} -> {grade}'
    if graded:
        return 'discarded', 'discarded ' + '; '.join(
            f'{source} -> {grade}' for source, grade in graded)
    return None, None


def capability_check_in(fn):
    """The capability governing this handler, or None.

    Drop-in replacement for the presence-matching version: same two
    recognised spellings, same string shapes ('decorator ...' /
    'in-handler ...'), but a call whose verdict is never consumed now reads as
    None -- which is what makes the ratchet a ratchet.
    """
    _kind, description = capability_check_detail(fn)
    kind = _kind
    return description if kind in ('decorator', 'in-handler') else None


def route_handlers(source_path, route_marker='_bp.route'):
    """(handler name, ast node) for every Flask route handler in one file."""
    tree = ast.parse(source_path.read_text(encoding='utf-8'), filename=str(source_path))
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(route_marker in ast.unparse(d) for d in fn.decorator_list):
            yield fn.name, fn
