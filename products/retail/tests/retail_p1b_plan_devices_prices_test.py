"""Drift guard: the add-on prices on the Plan & devices screen must be the
prices on the public pricing page.

plan-devices.js cannot import website/js/content.js (the website is not
served by the app), so it carries a mirror of three values. A mirror is
exactly the "two literals must match" shape that rots silently -- someone
re-prices the website and the owner's screen keeps quoting the old number
to a paying customer. This test reads BOTH files as text and compares the
values, so a change to either side alone goes red.

It compares parsed values, never one constant against another that moves
with it: the website numbers come out of content.js, the screen numbers out
of plan-devices.js, and neither file is imported.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONTENT_JS = ROOT / 'website' / 'js' / 'content.js'
PLAN_DEVICES_JS = ROOT / 'products' / 'retail' / 'frontend' / 'plan-devices.js'


def _website_pricing():
    text = CONTENT_JS.read_text(encoding='utf-8')
    block = re.search(r'var\s+PRICING\s*=\s*\{(.*?)\}', text, re.S)
    assert block, 'PRICING block not found in website/js/content.js'
    return dict(re.findall(r'(\w+)\s*:\s*"([^"]*)"', block.group(1)))


def _screen_prices():
    text = PLAN_DEVICES_JS.read_text(encoding='utf-8')
    block = re.search(r'var\s+PRICES\s*=\s*\{(.*?)\}', text, re.S)
    assert block, 'PRICES block not found in plan-devices.js'
    return dict(re.findall(r"(\w+)\s*:\s*'([^']*)'", block.group(1)))


def test_extra_device_price_matches_the_website():
    assert _screen_prices()['extraDevice'] == _website_pricing()['extra_device_price']


def test_extra_branch_price_matches_the_website():
    assert _screen_prices()['extraBranch'] == _website_pricing()['extra_branch_price']


def test_currency_matches_the_website():
    assert _screen_prices()['currency'] == _website_pricing()['currency_en']


def test_the_parsers_actually_find_values():
    """Guards the guard: an empty parse would make the three tests above
    compare KeyError-free nothing. Both sides must yield real, non-empty
    numeric prices."""
    site, screen = _website_pricing(), _screen_prices()
    for value in (site['extra_device_price'], site['extra_branch_price'],
                  screen['extraDevice'], screen['extraBranch']):
        assert value.isdigit() and int(value) > 0
