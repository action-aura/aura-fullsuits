"""Semver precedence vectors. The SAME vectors are asserted by Owner's
owner/tests/test_release_discovery.py against owner/app/releases/versioning.py:
the two comparators must agree, because Owner decides what to offer with one
and this product decides what to accept with the other."""
import itertools

import pytest

from commercial_runtime.updates.versioning import (
    compare_versions,
    is_newer,
    is_valid_semver,
    parse_semver,
)

ASCENDING = [
    "1.0.0-alpha",
    "1.0.0-alpha.1",
    "1.0.0-alpha.beta",
    "1.0.0-beta",
    "1.0.0-beta.2",
    "1.0.0-beta.11",
    "1.0.0-rc.1",
    "1.0.0-rc.9",
    "1.0.0-rc.10",
    "1.0.0",
    "1.0.1",
    "1.1.0",
    "2.0.0",
]

REJECTED = ["", "1.0", "01.0.0", "1.0.0-", "../1.0.0", "1.0.0/x", "1.0.0 rc", "1.0.0\n", "1.0.0-rc..1", "v1.0.0",
            "١.0.0"]


def test_every_adjacent_and_non_adjacent_pair_orders_correctly():
    # Every pair, not just neighbours: a comparator that is only locally right
    # (a < b, b < c, but c < a) would pass the neighbour check.
    for (i, a), (j, b) in itertools.combinations(enumerate(ASCENDING), 2):
        assert compare_versions(a, b) == -1, (a, b)
        assert compare_versions(b, a) == 1, (b, a)
        assert is_newer(b, a) and not is_newer(a, b)


def test_rc10_beats_rc9_which_plain_string_order_gets_wrong():
    assert is_newer("1.0.0-rc.10", "1.0.0-rc.9")
    assert "1.0.0-rc.10" < "1.0.0-rc.9"  # the trap: lexicographic order disagrees


def test_a_release_beats_any_prerelease_of_the_same_core():
    assert is_newer("1.0.0", "1.0.0-rc.99")


def test_build_metadata_is_ignored():
    assert compare_versions("1.0.0+abc", "1.0.0") == 0
    assert not is_newer("1.0.0+abc", "1.0.0")
    assert compare_versions("1.0.0+abc", "1.0.0+xyz") == 0


def test_equal_is_not_newer():
    assert not is_newer("1.0.0-rc.8", "1.0.0-rc.8")


@pytest.mark.parametrize("bad", REJECTED)
def test_malformed_versions_are_rejected(bad):
    with pytest.raises(ValueError):
        parse_semver(bad)
    assert not is_valid_semver(bad)


@pytest.mark.parametrize("bad", [None, 1, 1.0, b"1.0.0", ["1.0.0"]])
def test_non_strings_are_rejected(bad):
    with pytest.raises(ValueError):
        parse_semver(bad)
