"""The till's sale in progress must not outlive the PERSON, in a real browser.

The till keeps its cart when a cashier merely leaves the till screen. That same
survival used to carry across a sign-out and a user switch in the same window,
so the next cashier saw (and could charge) the previous cashier's sale.

Scenario (real Flask backend, real sign-up form, real sign-in form):
  1. Owner A rings up two items.
  2. ALLOW HALF -- A's session expires (server-side logout, then any API call
     brings up the relogin modal) and A signs back in as A: the cart is KEPT.
  3. A clicks Log Out WITH a sale in progress: the "A sale is in progress"
     dialog appears with Cancel focused; Cancel leaves the sale and the session.
  4. A confirms: signed out. Cashier B signs in on the same window: the cart is
     EMPTY, and the till screen B opens shows an empty cart.

Run with the SYSTEM Python:  py -3.14 products/retail/tests/retail_till_user_reset_e2e.py
  5. (step 4a) a deliberate sign-out clears the sale even when the SAME person signs back in.
Mutants (P1C_MUTATE=...): till_reset_logout_removed, till_shell_reset_noop, till_same_user_clears.
Note: the init() user-change check is a second layer behind logout's own reset, so removing
ONLY logout's reset still leaves 'different cashier' green (init() catches it) -- that is why
step 4a exists and why till_shell_reset_noop (kills both layers) is the proof for step 5.
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import retail_p1c_e2e_boot as B  # noqa: E402  (must come first: sets the AURA_* environment)

from playwright.sync_api import sync_playwright  # noqa: E402

A_EMAIL, A_PASS = "owner@p1c.local", "P1cTillTest12345!"
B_EMAIL, B_PASS = "cashier-b@p1c.local", "P1cCashierB123!"


def seed_cashier_b():
    """Direct INSERT into the running backend's own registry.db (the invite-link
    flow does not give a settable password) -- same technique as
    retail_p1a_shell_e2e._seed_cashier. The backend is in-process on the same
    AURA_APP_DATA, set by the boot module."""
    from commercial_runtime.identity import user_accounts
    from commercial_runtime.identity.registry_db import get_conn
    from commercial_runtime.security.passwords import hash_password

    conn = get_conn()
    try:
        company_id = conn.execute("SELECT company_id FROM users WHERE role='admin' LIMIT 1").fetchone()["company_id"]
        uid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
            "require_password_change, branch_scope_uid) VALUES (?,?,?,?,?,?,?,0,?)",
            (uid, company_id, f"EMP-{uuid.uuid4().hex[:6]}", B_EMAIL, hash_password(B_PASS), "cashier", "active", None),
        )
        conn.execute(
            "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), uid, "retail", "full"),
        )
        user_accounts.seed_capabilities_for_user(conn, uid, "cashier")
        conn.commit()
    finally:
        conn.close()


with sync_playwright() as p:
    browser, ctx, page = B.start_shop(p, viewport={"width": 1366, "height": 768})
    B.seed_catalogue(page)
    seed_cashier_b()
    B.go_pos(page)

    def cart_names():
        return page.evaluate("() => RetailSystem._cart.map(l => l.name)")

    def sign_in_through_modal(email, password):
        try:
            page.wait_for_selector("#aura-relogin-modal", timeout=10000)
        except Exception:
            state = page.evaluate(
                "() => [SubsystemApp._authPrompted, SubsystemApp._authModalOpen, SubsystemApp.active,"
                " document.querySelectorAll('.auth-overlay').length, location.href]")
            raise AssertionError("no sign-in modal appeared: " + str(state))
        page.wait_for_timeout(700)    # let any second render of the modal settle before typing
        page.fill("#rl-email", email)
        page.fill("#rl-pass", password)
        page.click("#rl-btn")
        try:
            page.wait_for_selector("#aura-relogin-modal", state="detached", timeout=6000)
        except Exception:
            raise AssertionError("sign-in as " + email + " did not complete; modal says: "
                                 + page.inner_text("#rl-error"))
        page.wait_for_selector("#aura-relogin-modal", state="detached", timeout=15000)
        page.wait_for_selector("#sub-header-section", timeout=20000)
        page.wait_for_timeout(800)

    def click_log_out():
        page.click("#sub-identity-chip-btn")
        page.click(".sub-identity-menu-item.is-danger")

    def step_ring_two_items():
        cards = page.locator("#pos-product-grid .pos-card")
        cards.nth(0).click()
        page.wait_for_function("() => RetailSystem._cart.length === 1", timeout=8000)
        cards.nth(1).click()
        page.wait_for_function("() => RetailSystem._cart.length === 2", timeout=8000)
        assert len(cart_names()) == 2, cart_names()

    def step_same_user_relogin_keeps_cart():
        before = cart_names()
        _r = page.request.post(B.BASE + "/api/auth/logout")        # the session dies server-side
        assert _r.status == 200, f"server-side sign-out failed: {_r.status}"
        # A unique URL each time: RetailSystem._get may answer from a short cache, and
        # a cached answer is exactly why the 401 sometimes never happened (flake).
        page.evaluate("() => fetch('/api/sub/retail/products?limit=1&nocache=' + Date.now(),"
                      " { credentials: 'include', cache: 'no-store' }).then(() => null, () => null)")
        sign_in_through_modal(A_EMAIL, A_PASS)               # ...and the SAME person signs back in
        after = cart_names()
        assert after == before and len(after) == 2, (
            f"a cashier who simply had to sign back in lost their sale: before={before} after={after}"
        )

    def step_logout_asks_and_cancel_keeps():
        click_log_out()
        page.wait_for_selector("#ret-confirm-dialog", timeout=5000)
        msg = page.inner_text("#ret-confirm-dialog-msg")
        assert "A sale is in progress. Signing out will clear it." in msg, f"wrong dialog text: {msg!r}"
        focused = page.evaluate("() => document.activeElement && document.activeElement.id")
        assert focused == "ret-confirm-cancel", f"Cancel must be focused on this destructive dialog, got {focused!r}"
        page.click("#ret-confirm-cancel")
        page.wait_for_selector("#ret-confirm-dialog", state="detached", timeout=5000)
        assert len(cart_names()) == 2, "Cancel cleared the sale"
        assert page.locator("#aura-relogin-modal").count() == 0, "Cancel still signed the cashier out"

    def confirm_log_out():
        click_log_out()
        page.wait_for_selector("#ret-confirm-dialog", timeout=5000)
        page.click("#ret-confirm-ok")

    def step_full_signout_clears_even_for_same_user():
        # A FULL sign-out (not a lock/expiry) ends the sale even when the same
        # person signs straight back in: the sign-out dialog warned them.
        confirm_log_out()
        sign_in_through_modal(A_EMAIL, A_PASS)
        assert cart_names() == [], f"a deliberate sign-out left the sale behind: {cart_names()}"

    def step_logout_then_other_user_sees_empty_till():
        B.go_pos(page)
        page.locator("#pos-product-grid .pos-card").nth(0).click()
        page.wait_for_function("() => RetailSystem._cart.length === 1", timeout=8000)
        confirm_log_out()
        sign_in_through_modal(B_EMAIL, B_PASS)
        who = page.evaluate("() => SubsystemApp.currentUser && SubsystemApp.currentUser.email")
        assert (who or "").lower() == B_EMAIL, f"expected cashier B to be signed in, got {who!r}"
        assert cart_names() == [], f"cashier B inherited A's sale: {cart_names()}"
        B.go_pos(page)
        assert cart_names() == [], f"the till screen cashier B opened holds A's sale: {cart_names()}"
        shown = page.evaluate("() => document.querySelectorAll('#pos-cart .pos-cart-row, #pos-cart [data-line]').length")
        assert shown == 0, f"A's lines are on screen for B ({shown})"
        draft = page.evaluate("() => [RetailSystem._posDraft, (document.getElementById('pos-disc') || {}).value]")
        assert not draft[0] and draft[1] in (None, "0", "", 0, "0.0"), f"A's discount/customer draft survived: {draft}"

    B.check("A rings up two items", step_ring_two_items)
    B.check("same user relogin after expiry KEEPS the cart (allow half)", step_same_user_relogin_keeps_cart)
    B.check("Log Out with a sale asks first; Cancel (focused) keeps everything", step_logout_asks_and_cancel_keeps)
    B.check("a full Log Out clears the sale even if the same person signs back in", step_full_signout_clears_even_for_same_user)
    B.check("after Log Out a DIFFERENT cashier gets an empty till", step_logout_then_other_user_sees_empty_till)
    browser.close()
    B.finish()
