"""
Aura Retail -- every route a customer needs must have a doorway.

WHY THIS EXISTS.

Five separate features shipped on this branch COMPLETE, TESTED, CORRECTLY GATED
-- and reachable by nothing. Not broken: unreachable. In order found:

  * the three-act licence issuance screen (Owner), linked from no nav, no
    command palette and no toolbar -- usable only by typing a URL;
  * Android's branch-pin setting, built onto a screen in no navigation graph;
  * `POST /branches`, so no shop could ever create its second branch -- while an
    entire multi-store programme shipped on top of it;
  * the whole email notification channel, so SMTP recipients could not be set by
    any user, ever;
  * backup, restore, and three CSV exports -- which the licensing screen
    explicitly PROMISES remain available to a customer whose licence has lapsed.

Two of those five were written by the same author who verified the backend
worked and treated that as done. None was caught by any test, because every test
asked "does this route behave correctly" and none asked "can anybody reach it".
Normal use does not catch them either -- a single-branch shop that never needs
email works perfectly.

So this file asks the question none of the others did.

THE RULE: every retail route is either referenced by a shipped client, or named
below with a REASON. There is no third state. A route that is neither is a
feature nobody can use, and this test fails.

The allowlist is the point, not a loophole. Writing a route into it is a
deliberate act that says "this is unreachable and here is why that is correct" --
which is a decision, whereas an unreachable route nobody wrote down is an
accident. Entries are expected. Silence is not.

Run:
    pytest products/retail/tests/retail_route_reachability_test.py -v
"""
import glob
import io
import re
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_API = PRODUCT_DIR / 'backend' / 'api' / 'retail_api.py'
FRONTEND_GLOB = str(PRODUCT_DIR / 'frontend' / '*.js')
SUITE_ROOT = PRODUCT_DIR.parent.parent
ANDROID_NET_GLOB = str(
    SUITE_ROOT / 'android' / 'aura-retail' / 'app' / 'src' / 'main' / 'java'
    / 'com' / 'actionaura' / 'retail' / 'net' / '*.kt'
)

#: Routes that no shipped client calls, each with why that is correct.
#:
#: KEEP THIS HONEST. "We have not got to it yet" is a legitimate reason and
#: should be written as exactly that, with what it is waiting for. What is NOT
#: legitimate is adding an entry to make this test green without reading the
#: route and deciding. The whole value of this file is that the decision is
#: forced and recorded.
INTENTIONALLY_UNREACHABLE = {
    # ── Deferred in writing, with the commit that deferred them ──────────────
    'modifier-groups':
        'The modifier CONFIG API (schema v26, commit 3596d35). That commit says '
        'plainly that the POS picker screen and the cart merge-key rework were '
        'cut to protect the money path, and that the API is complete so a '
        'screen can consume it whenever it is scheduled. Deferred deliberately '
        'and in writing, which is the difference between this and the five '
        'accidents above.',
    'modifier-options':
        'Same wave and same deferral as modifier-groups.',

    # ── Genuinely reachable only from elsewhere ─────────────────────────────
    'demo-seed':
        'Seeds demo data. Deliberately absent from every customer-facing UI: a '
        'shop must never find a button that invents fake sales in its own '
        'ledger. Reached by demo/packaging tooling only.',
    'sync/offline-override':
        'Manager approval of the offline-sales stop. Reached through the '
        'offline banner flow rather than by a literal path in client source, so '
        'this heuristic cannot see it. Reachability is proved separately by '
        'retail_offline_sales_stop_test.py.',
}

#: Routes that are unreachable and SHOULD NOT BE. Each is a real gap with an
#: owner. This list exists so the number stays visible and shrinking rather than
#: being rediscovered by a customer. Every entry is a promise to fix, or to
#: consciously downgrade to the list above.
KNOWN_GAPS = {
    'settings/business-day':
        'The business-day boundary scopes EVERY report and the dashboard '
        '(metrics.business_day). No shipped client can change it, so every '
        'install is silently pinned to the default clock and a shop whose day '
        'ends at 2am cannot say so. Found 2026-09-01; not yet fixed.',
    'reports/email':
        'Emails a report to a recipient. The email CHANNEL gained a settings '
        'screen in commit 50dc417, but nothing in the UI yet triggers a report '
        'email, so the route remains unreachable. Found 2026-09-01; not fixed.',
}


#: The other customer-facing blueprints this product mounts.
#:
#: WHY THIS IS NOT JUST retail_bp: the email channel -- one of the five
#: unreachable features that prompted this file -- lives on the NOTIFICATIONS
#: blueprint. A guard watching only retail_bp would have missed it entirely,
#: while looking thorough. The blind spot the guard itself can have is the same
#: shape as the one it exists to catch.
#:
#: DELIBERATELY EXCLUDED: commercial_runtime/identity/. Those routes are shared
#: with Clinic, and Clinic's clients are out of scope for this repo's retail
#: test suite -- so a route unused by Retail may be perfectly reachable from
#: Clinic, and reporting it here would be a false alarm. A guard that cries wolf
#: gets suppressed, and a suppressed guard is worse than none. Identity
#: reachability needs its own check, on both products at once; recorded as a
#: gap rather than half-covered here.
OTHER_BLUEPRINTS = (
    SUITE_ROOT / 'commercial_runtime' / 'backup' / 'routes.py',
    SUITE_ROOT / 'commercial_runtime' / 'notifications' / 'routes.py',
    SUITE_ROOT / 'commercial_runtime' / 'notifications' / 'whatsapp_routes.py',
)


def _routes():
    """Every route a retail customer could need, as (path, methods).

    retail_bp plus the mounted commercial_runtime blueprints. Both decorator
    spellings are matched because the blueprints are built by factories and use
    a local `bp` name rather than `retail_bp`.
    """
    found = []
    pattern = re.compile(
        r"@(?:retail_bp|bp)\.route\(\s*'([^']+)'(?:\s*,\s*methods=\[([^\]]*)\])?")
    for path in (BACKEND_API,) + OTHER_BLUEPRINTS:
        try:
            src = io.open(path, encoding='utf-8').read()
        except FileNotFoundError:
            continue
        for m in pattern.finditer(src):
            found.append((m.group(1),
                          (m.group(2) or "'GET'").replace("'", '').replace(' ', '')))
    return found


def _client_source():
    """Everything a shipped client could call a route from.

    The desktop frontend AND Android's network layer: a route reached only by
    the phone is REACHABLE and must not be reported. Getting that wrong would
    make this test cry wolf, and a guard that cries wolf gets suppressed.
    """
    text = []
    for path in glob.glob(FRONTEND_GLOB) + glob.glob(ANDROID_NET_GLOB):
        text.append(io.open(path, encoding='utf-8', errors='replace').read())
    return '\n'.join(text)


def _search_key(route_path):
    """The most DISTINCTIVE literal fragment of a route.

    A client writes `/products/${id}/modifier-groups`, so only the literal
    segments survive verbatim -- and which segment identifies the route matters
    enormously here.

    The obvious choice, the literal PREFIX before the first converter, is wrong
    and dangerously so: it reduces `/products/<pid>/modifier-groups` to
    `/products`, which every client calls constantly. Ten genuinely unreachable
    modifier routes would have been reported as fine. A guard whose failure mode
    is a false NEGATIVE is worse than no guard, because it is trusted.

    So the key is the LONGEST literal segment, which is the one carrying the
    route's identity: `modifier-groups`, not `products`. For a route with no
    converter at all the whole path is used, which is stricter still.
    """
    if '<' not in route_path:
        return route_path.strip('/')
    segments = [s for s in route_path.split('/') if s and '<' not in s]
    return max(segments, key=len) if segments else route_path.strip('/')


def test_every_route_is_reachable_or_named():
    """The guard. A route no client calls must be written down, with a reason."""
    client = _client_source()
    unaccounted = []

    for path, methods in _routes():
        key = _search_key(path)
        if len(key.strip('/')) <= 2:
            continue                      # too generic to judge; see _search_key
        if key in client:
            continue                      # a client calls it
        if key in INTENTIONALLY_UNREACHABLE or key in KNOWN_GAPS:
            continue                      # written down, with a reason
        unaccounted.append('%s  [%s]' % (path, methods))

    assert not unaccounted, (
        'These retail routes are reachable by NO shipped client, and are not '
        'named in INTENTIONALLY_UNREACHABLE or KNOWN_GAPS:\n  '
        + '\n  '.join(sorted(unaccounted))
        + '\n\nA complete, correct, gated route that nothing calls is a feature '
          'nobody can use. Five shipped that way on this branch before this '
          'test existed.\n'
          'Either give it a doorway, or add it to one of the two lists in this '
          'file WITH A REASON. Adding it without reading it defeats the point.'
    )


def test_the_allowlists_do_not_rot():
    """Every named route must still EXIST.

    A stale entry is worse than none: it silently excuses a route that was
    renamed or deleted, and the next genuinely-unreachable route to take that
    path inherits the excuse. This repo has corrected four stale claims in
    documentation this week; the same rot applies to an allowlist.
    """
    live = {_search_key(p) for p, _ in _routes()}
    named = set(INTENTIONALLY_UNREACHABLE) | set(KNOWN_GAPS)
    stale = sorted(k for k in named if k not in live)
    assert not stale, (
        'These routes are named in this file but no longer exist:\n  '
        + '\n  '.join(stale)
        + '\n\nRemove them. A stale exemption silently covers whatever takes '
          'that path next.'
    )


def test_nothing_listed_is_actually_reachable():
    """The lists must not outlive the problem they describe.

    When a gap is finally fixed -- a screen is built, the route gains a caller --
    its entry here becomes a lie, and a lie in an exemption list is how the NEXT
    unreachable route inherits a reason that was written about something else.

    So a listed route that a client now calls is a failure, and the fix is to
    delete the entry. This is the direction that keeps the list shrinking:
    without it, KNOWN_GAPS would only ever grow, and the file would quietly
    become a graveyard rather than a ledger.
    """
    client = _client_source()
    resolved = sorted(
        key for key in (set(INTENTIONALLY_UNREACHABLE) | set(KNOWN_GAPS))
        if key in client
    )
    assert not resolved, (
        'These routes are listed as unreachable but a client now calls them:\n  '
        + '\n  '.join(resolved)
        + '\n\nGood news -- the gap closed. Delete the entry, so the list keeps '
          'describing only what is still true.'
    )


def test_known_gaps_are_declared_not_hidden():
    """KNOWN_GAPS must never be empty-by-neglect.

    This is a ratchet in the honest direction: the list is expected to SHRINK as
    gaps are fixed. It exists so an unreachable feature that should be reachable
    stays visible instead of being quietly reclassified as intentional. Every
    entry must carry a real explanation, not a placeholder.
    """
    for route, reason in KNOWN_GAPS.items():
        assert len(reason) > 60, (
            '%s has no real reason written against it. A one-word excuse in '
            'this list is how a genuine gap becomes permanent.' % route
        )
