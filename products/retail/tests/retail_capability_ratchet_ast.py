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

1. NOT INTERPROCEDURAL, WITH ONE NAMED EXCEPTION (2026-08-24, AUDIT-032
   follow-on). `abort_unless(session_has_capability(CODE))` -- a helper that
   raises -- is still a real gate this module scores as `packaged`, i.e. NOT
   consumed. It cannot see inside `abort_unless`. That is still the
   fail-closed direction for a ratchet (a false ALARM, never a false pass),
   and still a limit for that shape.

   But ONE interprocedural shape is now followed, because production grew
   it and the fail-closed direction turned into a false alarm against a
   real, live gate: a helper that returns its verdict as one element of a
   tuple --

       def _session_read_scope(sess):
           ...
           return False, (session_has_capability(CAP_REPORTS)
                          or session_has_capability(CAP_CASH_APPROVE))

   -- read on its own, packages the verdict into a `Tuple` (honest limit 5,
   unchanged: a bare tuple is one of the "anything else" shapes this module
   gives up at). But `_session_read_scope`'s two real callers destructure
   that exact tuple and branch on the second slot --

       _mine, may_read = _session_read_scope(sess)
       if not may_read:
           return jsonify({'status': 'error', 'message': FOREIGN_DRAWER_MESSAGE}), 403

   -- which is genuinely, load-bearingly gated: it decides whether one till
   may read another till's takings. Grading the helper's call in isolation
   said `packaged`; the ratchet built on that verdict would have reported a
   real, shipped, security-relevant check as absent -- exactly the false
   alarm limit 1 warns is the failure MODE this module is allowed to have,
   which does not make an actual instance of it something to ship silently.

   `graded_capability_calls_in_module()` is the module-aware entry point
   that follows this ONE shape: a `return a, b, ...` tuple, one slot of
   which carries a capability verdict (directly, or through the same
   value-preserving wrappers and name-binding `grade_value` already
   understands), destructured by a caller as `x, y, ... = helper(...)` with
   a same-position plain-`Name` target, and THAT name graded by the exact
   same single-function machinery as everything else -- so all of that
   machinery's OWN honest limits (4: name shadowing; 5: no positional
   matching through a second layer of packaging; 7: inert-but-present
   branches) apply again, once per caller, unchanged.

   Deliberately NOT widened past that: a verdict packaged into a `dict`,
   consumed by a decorator-wrapped helper, threaded through a second call
   before reaching a decision, or read by a caller that itself only reads
   the name inside ANOTHER helper's tuple return (two hops) all still grade
   `packaged`, on purpose, for the same reason limit 1 gives -- proving
   arbitrary data flow through arbitrary Python is undecidable, and this
   module chases exactly the shapes production actually uses and no
   further, per the reduced claim below.

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
#: The verdict is packaged into a tuple this function RETURNS, and a caller
#: that destructures that tuple goes on to consume the matching slot -- see
#: the interprocedural section of honest limit 1. Kept distinct from
#: CONSUMED_BRANCH/RETURN/RAISE (rather than reporting whichever of those the
#: CALLER happened to use) because "you have to look at the caller to see
#: why this is gated" is a genuinely different fact than "the gate is right
#: here", and collapsing them would hide that from whoever reads a grade.
CONSUMED_VIA_CALLER = 'via-caller'
UNCONSUMED_DISCARDED = 'discarded'
UNCONSUMED_PACKAGED = 'packaged'
#: The verdict reached a branch, and the branch does NOTHING -- every arm is
#: `pass` / `...` / a bare string. `if not session_has_capability(C): pass` is
#: the shortest defeat of a reaches-a-decision rule, and it gates exactly as
#: much as the dead line did. See honest limit 7 for what remains open past it.
UNCONSUMED_INERT = 'inert-branch'

CONSUMING_GRADES = frozenset({CONSUMED_BRANCH, CONSUMED_RETURN, CONSUMED_RAISE, CONSUMED_VIA_CALLER})
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


def grade_value(node, root, parents=None, _seen=None, on_tuple_return=None):
    """How the value produced by `node` is used, as one of six grades.

    Walks UP from the node. At each step the only question is what role the
    value plays in its parent: a decision (stop, consumed), a binding (switch
    to following the name), a wrapper (keep climbing, same value), a tuple
    RETURN slot (ask `on_tuple_return`, see below), or anything else (stop,
    not consumed).

    `on_tuple_return`, when given, is called as `on_tuple_return(index)` the
    moment climbing reaches "this is element `index` of a tuple that is a
    `return` statement's whole value" -- the interprocedural on-ramp described
    in honest limit 1's 2026-08-24 addendum. It must answer with a grade
    string (typically CONSUMED_VIA_CALLER) if some caller consumes that slot,
    or None to fall through to the ordinary UNCONSUMED_PACKAGED a bare tuple
    gets otherwise. Left as None (the default, and what every single-function
    caller in this module still passes) this function's behaviour toward a
    tuple return is byte-for-byte what it always was.
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
            return _grade_names(_assigned_names(parent), root, parents, _seen, on_tuple_return)
        if isinstance(parent, ast.NamedExpr) and parent.value is child:
            # A walrus both binds AND yields, so try the binding first and
            # otherwise keep climbing with the inline value.
            via_name = _grade_names([parent.target.id], root, parents, _seen, on_tuple_return)
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

        # ── one slot of a tuple this function RETURNS: ask the caller ────────
        # `return a, (session_has_capability(C) or ...)` -- read alone this is
        # just another "anything else" packaging (a Tuple), but a caller that
        # destructures the return and consumes the matching slot genuinely did
        # gate on this verdict. Only asked when a resolver was supplied (the
        # module-aware entry point below); every single-function caller in
        # this module passes None, so this branch is inert for them and they
        # fall straight through to UNCONSUMED_PACKAGED exactly as before. See
        # honest limit 1's 2026-08-24 addendum.
        if (on_tuple_return is not None and isinstance(parent, ast.Tuple)
                and isinstance(parents.get(parent), ast.Return)
                and parents[parent].value is parent):
            for index, elt in enumerate(parent.elts):
                if elt is child:
                    resolved = on_tuple_return(index)
                    if resolved is not None:
                        return resolved
                    break

        # ── anything else PACKAGES the value ────────────────────────────────
        # A call argument, a dict/list/set/tuple, an f-string, a subscript, an
        # attribute access. The value went somewhere this module cannot follow,
        # so it does not count as a gate. See honest limit 1.
        return UNCONSUMED_PACKAGED


def _grade_names(names, root, parents, seen, on_tuple_return=None):
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
                grade = grade_value(node, root, parents, seen, on_tuple_return)
                if grade in CONSUMING_GRADES:
                    return grade
                if grade == UNCONSUMED_PACKAGED:
                    # Read, but into somewhere unfollowable -- still better
                    # evidence than "never read at all", and the distinction
                    # is what makes a ratchet failure diagnosable.
                    best = UNCONSUMED_PACKAGED
    return best


def graded_capability_calls(fn):
    """[(source of each session_has_capability call, its grade)], in source order.

    Single-function only -- no interprocedural following. This is still what
    `capability_check_detail`/`capability_check_in` use for "is THIS handler
    gated", and it is what every synthetic single-function test in the suite
    exercises. `graded_capability_calls_in_module` below is the wider,
    module-aware sibling; this one is unchanged on purpose so nothing that
    already depends on its exact behaviour moves.
    """
    parents = parent_map(fn)
    return [(ast.unparse(node), grade_value(node, fn, parents))
            for node in ast.walk(fn) if is_capability_call(node)]


def _calls_named(call, name):
    """Same two spellings `is_capability_call` recognises (bare name, dotted
    attribute), generalised to any callee -- used below to find call sites of
    a specific LOCAL helper rather than of `session_has_capability` itself."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == name
    if isinstance(func, ast.Attribute):
        return func.attr == name
    return False


def _enclosing_function(node, parents):
    """The nearest FunctionDef ANCESTOR of `node` within the subtree `parents`
    was built for -- which for a call in the root function's own body IS that
    root function (`parent_map` records the root as the parent of its own body
    statements), and for a call one `def` deeper is that inner function.
    None only if `node` has no function ancestor at all.

    Exists for one reason: `ast.walk(fn)` descends into functions DEFINED
    inside `fn`, and a `return` inside a nested function returns through the
    NESTED function, not through `fn`. Without this, a capability verdict in

        def _read_scope(sess):
            def inner():
                return True, session_has_capability(CAP_REPORTS)
            return inner

    was credited as `via-caller` off `_read_scope`'s callers -- callers that
    unpack `_read_scope`'s OWN return (a function object) and have never seen
    `inner`'s tuple at all. A false CREDIT is the one direction a ratchet may
    not fail in, so the resolver is simply not offered for nested calls; they
    fall back to the ordinary `packaged`, which is what they were before the
    interprocedural pass existed.
    """
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
        current = parents.get(current)
    return None


def _grade_tuple_return_via_callers(helper_name, index, functions, parents_by_fn):
    """Best grade reachable by following slot `index` of `helper_name`'s
    return tuple into every OTHER function in `functions` that destructures a
    direct call to it, then grading what that caller does with the bound
    name.

    Deliberately narrow, matching only the shape production actually uses:

      * the caller assigns FROM a bare call to `helper_name` -- `x, y =
        helper(...)` or `x, y = mod.helper(...)` -- not a call wrapped in
        anything else, which would be a second layer of packaging this
        module still cannot see through (honest limit 1, one hop out);
      * the assignment target is a plain `Tuple`/`List` of `Name`s -- no
        starred or nested targets, which stay unresolved rather than guessed
        at (mirrors honest limit 5's "positional matching is not worth
        building" stance, now one layer removed from where it was written);
      * the target at `index` is graded by NAME over the CALLER's own body,
        via the exact same `_grade_names` pass the single-function via-a-name
        case uses -- so limit 4 (name shadowing: by identifier, no scope or
        ordering analysis) applies again here, once per caller, unchanged;
      * …with one place limit 4 is NOT merely inherited but explicitly
        fail-closed: if the module defines `helper_name` more than once, no
        caller can be attributed to a particular definition, and this
        function credits NONE of them (see the guard below). The single-
        function machinery has no equivalent situation to be wrong about;
        this one is created by looking across functions, so it is answered
        here rather than left to whoever reads the grade.

    Stops at the FIRST caller that consumes it. A caller that destructures
    the tuple and drops the name is not evidence of anything -- other callers
    may still genuinely gate on it, and each caller's OWN single-function
    analysis (via `route_handlers` + `capability_check_in`, if that caller is
    itself a route) independently judges whether THAT caller's handling is
    gated. This function only answers "does the helper's returned slot reach
    a decision ANYWHERE", which is the question `on_tuple_return` was asked.
    """
    # AMBIGUOUS NAME -> NO CREDIT. Call sites are matched by identifier (this
    # module has no scope or type analysis -- honest limit 4), so if the module
    # defines this name twice, `x, y = _read_scope(...)` could be unpacking
    # EITHER of them, and crediting the one that happens to hold a capability
    # call would be inventing a gate out of a name collision. Declining is the
    # fail-closed answer: the check reverts to `packaged` and a human looks.
    if sum(1 for name, _fn in functions if name == helper_name) > 1:
        return None

    for caller_name, caller_fn in functions:
        if caller_name == helper_name:
            continue  # no self-recursion; a helper cannot be its own caller here
        caller_parents = parents_by_fn[id(caller_fn)]
        for node in ast.walk(caller_fn):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, (ast.Tuple, ast.List)):
                continue
            if not (isinstance(node.value, ast.Call) and _calls_named(node.value, helper_name)):
                continue
            if index >= len(target.elts) or not isinstance(target.elts[index], ast.Name):
                continue  # out of range, or a nested/starred target -- not tracked
            grade = _grade_names([target.elts[index].id], caller_fn, caller_parents, set())
            if grade in CONSUMING_GRADES:
                return CONSUMED_VIA_CALLER
    return None


def graded_capability_calls_in_module(module_tree):
    """[(function name, call source, grade)] for every session_has_capability
    call in a whole module -- `graded_capability_calls` run per function, PLUS
    the one interprocedural shape described in honest limit 1's 2026-08-24
    addendum: a verdict a helper RETURNS as one element of a tuple, followed
    into every caller that destructures that exact tuple.

    This is the entry point the real-file tests use (`_real_calls` in
    retail_capability_ratchet_consumption_test.py); `capability_check_in` and
    everything downstream of it for a SINGLE handler still goes through
    `graded_capability_calls`/`capability_check_detail` unchanged, because
    `_session_read_scope` is a helper, not a `_bp.route` handler, and never
    appears in `route_handlers`'s output either way.
    """
    # A LIST of (name, node), not a name-keyed dict, and parents keyed by the
    # node's identity. Keyed by name, two functions sharing a name -- a method
    # on two classes, a helper redefined under a feature flag, a nested
    # `def _row(...)` inside two different handlers -- collapsed to ONE entry
    # and the loser was never analysed AT ALL: its capability calls vanished
    # from this sweep silently, which is the fail-OPEN direction (the whole
    # point of the real-file test is that no shipped check goes unexamined).
    # retail_api.py has no duplicate names today, so nothing was actually being
    # dropped; nothing pins that, and a silent drop is not something to leave
    # armed in a security ratchet.
    functions = [(fn.name, fn) for fn in ast.walk(module_tree)
                 if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))]
    parents_by_fn = {id(fn): parent_map(fn) for _name, fn in functions}

    out = []
    for name, fn in functions:
        parents = parents_by_fn[id(fn)]

        def resolver(index, _name=name):
            return _grade_tuple_return_via_callers(_name, index, functions, parents_by_fn)

        for node in ast.walk(fn):
            if is_capability_call(node):
                # The interprocedural resolver is offered ONLY for calls in
                # `fn`'s own body. One nested a function deeper returns through
                # THAT function, so `fn`'s callers are no evidence about it --
                # see `_enclosing_function`.
                in_fn_itself = _enclosing_function(node, parents) in (None, fn)
                grade = grade_value(node, fn, parents,
                                    on_tuple_return=resolver if in_fn_itself else None)
                out.append((name, ast.unparse(node), grade))
    return out


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
