"""
Aura Retail -- licensing failure copy: clock advice must be scoped, and the
three copies of the reason-code buckets must stay in step.

THE BUG (2026-09-04). One message served all twelve LOCAL_VERIFICATION reason
codes and told every one of them:

    "Check that this computer's date and time are correct"

For nine of the twelve that is not merely unhelpful, it is false. A real
Mi Note 10 stranded on UNKNOWN_SIGNING_KEY showed exactly that sentence, and
it sent an investigation off to compare clocks -- which matched to the
identical epoch second -- while the real cause was a trust_store.json seeded
once in 2026-08 that permanently shadowed every corrected trust anchor shipped
afterwards. The screen cost more time than the defect did, and a customer
hitting the same state would have been given an instruction that can never
work, with no code to quote to support.

WHY A STRUCTURAL TEST. licensing.js and app-shell.js are vanilla browser
scripts (no bundler, no module system, nothing to import -- see licensing.js's
own header), and LicensingMessages.kt is on the JVM side. The three buckets are
duplicated ON PURPOSE and their own comments say "keep the two in step". There
is no runtime that can hold all three at once, so the invariant is asserted
against the sources, which is also how the original discoverability bug in this
directory was found (retail_licensing_nav_discoverability_test.py).

The Kotlin behaviour itself is unit-tested for real in
android/aura-retail/.../LicensingMessagesTest.kt. This file guards the thing
that unit test cannot see: drift BETWEEN the three copies.

Run:
    pytest products/retail/tests/retail_licensing_message_scoping_test.py -v
"""
import re
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
SUITE_ROOT = PRODUCT_DIR.parent.parent
FRONTEND_DIR = PRODUCT_DIR / 'frontend'
KOTLIN_MESSAGES = (
    SUITE_ROOT / 'android' / 'aura-retail' / 'app' / 'src' / 'main' / 'java'
    / 'com' / 'actionaura' / 'retail' / 'licensing' / 'LicensingMessages.kt'
)

CLOCK_SENTENCE = 'date and time are correct'

# The only three a customer can resolve themselves, and so the only three for
# which the clock sentence is true. Spelled out here rather than read from one
# of the sources, so a wrong edit to any single source cannot make this test
# agree with it.
EXPECTED_CLOCK_FIXABLE = {
    'ASSERTION_EXPIRED',
    'ASSERTION_NOT_YET_VALID',
    'CLOCK_ROLLBACK_SUSPECTED',
}


def _block_after(source: str, identifier: str) -> str:
    """The braces/parens block introduced by `identifier`, brace-matched.

    Deliberately not a regex over the whole block: these files nest object
    literals, and a lazy match would silently stop at the first '}' and read a
    truncated set as the real one -- a passing test over half the data.
    """
    start = source.index(identifier)
    opener = min(
        (source.index(c, start) for c in '({' if c in source[start:start + 400]),
        default=-1,
    )
    assert opener != -1, f'no opening bracket after {identifier!r}'
    pairs = {'(': ')', '{': '}'}
    close = pairs[source[opener]]
    depth = 0
    for i in range(opener, len(source)):
        if source[i] == source[opener]:
            depth += 1
        elif source[i] == close:
            depth -= 1
            if depth == 0:
                return source[opener:i + 1]
    raise AssertionError(f'unbalanced block after {identifier!r}')


def _codes_in(source: str, identifier: str) -> set:
    return set(re.findall(r'\b[A-Z][A-Z0-9_]{4,}\b', _block_after(source, identifier)))


def _flatten(source: str) -> str:
    """Join adjacent string literals and collapse wrapping whitespace.

    All three files wrap long copy across concatenated literals, so the
    sentences below exist in the rendered message but never contiguously in
    the source. Without this, an assertion on the copy silently tests the
    line-wrapping instead of the words.
    """
    joined = re.sub(r"['\"]\s*\+\s*['\"]", '', source)
    return re.sub(r'\s+', ' ', joined)


def _sources():
    return {
        'licensing.js': (FRONTEND_DIR / 'licensing.js').read_text(encoding='utf-8'),
        'app-shell.js': (FRONTEND_DIR / 'app-shell.js').read_text(encoding='utf-8'),
        'LicensingMessages.kt': KOTLIN_MESSAGES.read_text(encoding='utf-8'),
    }


BUCKET_IDENTIFIERS = {
    'licensing.js': ('LOCAL_VERIFICATION_REASON_CODES = ', 'CLOCK_FIXABLE_REASON_CODES = '),
    'app-shell.js': ('ACTIVATION_LOCAL_VERIFICATION_REASONS:', 'ACTIVATION_CLOCK_FIXABLE_REASONS:'),
    'LicensingMessages.kt': ('LOCAL_VERIFICATION_REASON_CODES: Set<String>', 'CLOCK_FIXABLE_REASON_CODES: Set<String>'),
}


def test_all_three_copies_list_the_same_local_verification_codes():
    """A code added to one copy and not the others is invisible at runtime:
    each file is loaded by a different process, so the drift only shows up as
    one screen giving different advice than another for the same failure."""
    sources = _sources()
    buckets = {
        name: _codes_in(sources[name], BUCKET_IDENTIFIERS[name][0])
        for name in sources
    }
    reference = buckets['licensing.js']
    assert len(reference) >= 12, f'bucket looks truncated: {sorted(reference)}'
    for name, codes in buckets.items():
        assert codes == reference, (
            f'{name} local-verification codes drifted from licensing.js: '
            f'only in {name}={sorted(codes - reference)}, missing={sorted(reference - codes)}'
        )


def test_clock_fixable_set_is_exactly_the_three_a_customer_can_fix():
    sources = _sources()
    for name, source in sources.items():
        codes = _codes_in(source, BUCKET_IDENTIFIERS[name][1])
        assert codes == EXPECTED_CLOCK_FIXABLE, (
            f'{name} clock-fixable set is {sorted(codes)}, expected {sorted(EXPECTED_CLOCK_FIXABLE)}'
        )


def test_clock_fixable_codes_are_a_subset_of_the_local_verification_bucket():
    """Otherwise the clock branch guards codes that never reach it, and the
    scoping above would be decorative."""
    sources = _sources()
    for name, source in sources.items():
        local_ids, clock_ids = BUCKET_IDENTIFIERS[name]
        assert _codes_in(source, clock_ids) <= _codes_in(source, local_ids), name


def test_the_clock_sentence_is_gated_and_not_the_unconditional_answer():
    """The actual regression.

    Two halves, both needed. The file must still be ABLE to give clock advice
    -- deleting the sentence outright would be wrong for the three codes it is
    genuinely true for -- AND the clock-fixable set must actually gate
    something, which is what a single unconditional message did not do. A set
    that is merely DEFINED and never referenced would leave the old behaviour
    intact while looking fixed, so require a use beyond the definition.
    """
    for name, source in _sources().items():
        flat = _flatten(source)
        assert CLOCK_SENTENCE in flat, f'{name} lost the clock advice entirely'
        clock_identifier = BUCKET_IDENTIFIERS[name][1].split(':')[0].split(' ')[0]
        uses = source.count(clock_identifier)
        assert uses >= 2, (
            f'{name} defines {clock_identifier} but never branches on it '
            f'(found {uses} occurrence(s)); the clock sentence would still be '
            'shown for codes a clock cannot possibly cause'
        )


def test_non_clock_failures_tell_the_user_which_code_to_quote():
    """The reason code is the fastest route to the cause and is already written
    to licensing_events. Withholding it is what forced pulling a database off a
    handset to learn it."""
    for name, source in _sources().items():
        assert 'quote this code' in _flatten(source), (
            f'{name} does not surface the reason code on a non-clock local failure'
        )
