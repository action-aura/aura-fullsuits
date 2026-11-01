"""2026-11-01 -- every Aura price INCLUDES the 16% Jordan general sales tax.

Accountant decision (delegated by the owner): the customer is never charged the
tax on top, so no price and no total may change. The invoice just SHOWS the tax
inside the price: net = total / 1.16, tax = total - net, net + tax == total.

What each group guards (and the wrong version it rejects):
  - split_tax_inclusive: net + tax == gross exactly (a tax rounded on its own
    can drift a cent); 250 -> 215.52 + 34.48 (the columns are Numeric(12,2), so
    the split is at the cent -- see calculator.split_tax_inclusive);
  - a new invoice stores the tax INSIDE an unchanged total (the wrong version
    adds it on top: total 290, or stores net as the subtotal);
  - the seller's commission is byte-identical to before: it is 10% of the price
    paid (the wrong version that stores net as subtotal cut it by 13.8%, because
    commissions/ledger.py reads (subtotal - discount) / total);
  - the invoice page says it, in English and Arabic, and an OLD invoice
    (tax_total 0) gets the same breakdown computed from its total.
"""
from __future__ import annotations

from decimal import Decimal


from tests.conftest import force_login
from tests.test_step0_packages import _confirm, _only_order, _post_sale, _world

D = Decimal


def _invoice_for(app, order_id):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice, SalesOrder
        from app.models.commissions import CommissionLedgerEntry

        order = db_session.get(SalesOrder, order_id)
        inv = db_session.execute(select(CommercialInvoice).where(CommercialInvoice.sales_order_id == order_id)).scalars().one()
        entries = db_session.execute(
            select(CommissionLedgerEntry).where(CommissionLedgerEntry.source_commercial_invoice_id == inv.id)
        ).scalars().all()
        return {
            "id": inv.id, "subtotal": D(inv.subtotal), "discount": D(inv.discount_total), "tax": D(inv.tax_total),
            "total": D(inv.total), "order_total": D(order.total), "status": inv.status,
            "commissions": [(D(e.base_amount), D(e.commission_amount)) for e in entries],
        }


def _sell(client, app, w, *, amount, phones=None):
    resp = _post_sale(client, app, w, "p1", amount=amount, phones=phones)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:1500]
    order_id = _only_order(app)
    _confirm(app, order_id, w["manager"])
    return order_id


# ------------------------------------------------------------- the helper --

def test_split_tax_inclusive_known_values():
    from app.commercial_sales.calculator import split_tax_inclusive

    assert split_tax_inclusive(D("250.00")) == (D("215.52"), D("34.48"))
    assert split_tax_inclusive(D("50.00")) == (D("43.10"), D("6.90"))
    assert split_tax_inclusive(D("300.00")) == (D("258.62"), D("41.38"))
    assert split_tax_inclusive(D("0")) == (D("0.00"), D("0.00"))


def test_split_tax_inclusive_always_adds_back_to_the_total_exactly():
    """The tax is the REMAINDER of the total, never rounded on its own: every
    cent amount up to 600.00 must add back to itself, and the tax must stay
    inside 13.7%-13.9% of the gross for any gross that is not tiny."""
    from app.commercial_sales.calculator import split_tax_inclusive

    for cents in range(0, 60001):
        gross = D(cents) / 100
        net, tax = split_tax_inclusive(gross)
        assert net + tax == gross, gross
        assert net >= 0 and tax >= 0, gross
        if cents >= 1000:
            assert D("0.137") < tax / gross < D("0.139"), gross


def test_the_rate_is_a_named_constant_not_a_magic_number():
    from app.commercial_sales import calculator

    assert calculator.GENERAL_SALES_TAX_RATE == D("0.16")
    # A different rate must change the split (the helper really reads it).
    assert calculator.split_tax_inclusive(D("100.00"), D("0.10")) == (D("90.91"), D("9.09"))


# ------------------------------------------------------- a real P1 sale --

def test_p1_sale_stores_the_tax_inside_an_unchanged_total(app, client, seeded):
    w = _world(app)
    order_id = _sell(client, app, w, amount="250.00")
    inv = _invoice_for(app, order_id)
    assert inv["order_total"] == D("250.00") and inv["total"] == D("250.00")      # nothing added on top
    assert inv["tax"] == D("34.48")                                                # the tax inside it
    assert inv["subtotal"] == D("250.00") and inv["discount"] == D("0.00")
    assert inv["total"] - inv["tax"] == D("215.52")                                # net
    assert inv["status"] == "PAID"


def test_p1_with_a_phone_splits_the_whole_300_and_changes_no_price(app, client, seeded):
    w = _world(app)
    order_id = _sell(client, app, w, amount="300.00", phones=1)
    inv = _invoice_for(app, order_id)
    assert inv["total"] == inv["order_total"] == D("300.00")
    assert (inv["total"] - inv["tax"], inv["tax"]) == (D("258.62"), D("41.38"))


def test_sellers_commission_is_identical_to_before_tax_was_recorded(app, client, seeded):
    """RED if the net is stored as the subtotal: the ledger's taxable fraction
    becomes 215.52/250 and the 25.00 commission falls to 21.55. The numbers are
    the ones this sale paid BEFORE this change (test_step0_packages: 25.00 on a
    250 sale; the phone's 50 never earns, so 300 + 1 phone also earns on 250)."""
    w = _world(app)
    order_id = _sell(client, app, w, amount="250.00")
    assert _invoice_for(app, order_id)["commissions"] == [(D("250.00"), D("25.00"))]


def test_sellers_commission_with_an_extra_phone_is_identical_too(app, client, seeded):
    w = _world(app)
    order_id = _sell(client, app, w, amount="300.00", phones=1)
    assert _invoice_for(app, order_id)["commissions"] == [(D("250.00"), D("25.00"))]


# ------------------------------------------------------------ the page --

def _page(client, app, w, invoice_id, lang):
    force_login(client, app, w["admin"])
    resp = client.get(f"/invoices/{invoice_id}", headers={"Accept-Language": lang})
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_invoice_page_shows_the_tax_inside_the_total_in_english(app, client, seeded):
    w = _world(app)
    inv = _invoice_for(app, _sell(client, app, w, amount="250.00"))
    html = _page(client, app, w, inv["id"], "en")
    assert "Prices include 16% general sales tax" in html
    assert "Sales tax 16% (included)" in html
    assert "215.52" in html and "34.48" in html and "250" in html
    assert "<dt>Tax</dt>" not in html and "<dt>Subtotal</dt>" not in html   # the misleading rows are gone


def test_invoice_page_says_it_in_arabic(app, client, seeded):
    w = _world(app)
    inv = _invoice_for(app, _sell(client, app, w, amount="250.00"))
    html = _page(client, app, w, inv["id"], "ar")
    assert "الأسعار شاملة ضريبة المبيعات العامة 16%" in html
    assert "ضريبة المبيعات 16% (مشمولة في السعر)" in html
    assert "Prices include" not in html


def test_an_old_invoice_with_tax_total_zero_shows_the_same_breakdown(app, client, seeded):
    """An invoice made before tax was recorded was priced tax-inclusive too: its
    page computes the breakdown from its total and leaves the row alone."""
    w = _world(app)
    inv = _invoice_for(app, _sell(client, app, w, amount="250.00"))
    with app.app_context():
        from sqlalchemy import update

        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice

        db_session.execute(update(CommercialInvoice).where(CommercialInvoice.id == inv["id"]).values(tax_total=D("0.00")))
        db_session.commit()
    html = _page(client, app, w, inv["id"], "en")
    assert "Sales tax 16% (included)" in html and "215.52" in html and "34.48" in html
    with app.app_context():
        from app.commercial_sales.invoices import invoice_tax_breakdown
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice

        row = db_session.get(CommercialInvoice, inv["id"])
        assert D(row.tax_total) == D("0.00")                                    # stored row untouched
        b = invoice_tax_breakdown(row)
        assert (b["net"], b["tax"], b["total"]) == (D("215.52"), D("34.48"), D("250.00"))
        assert b["net"] + b["tax"] == b["total"]


def test_api_invoice_exposes_the_included_tax_and_the_net(app, client, seeded):
    w = _world(app)
    inv = _invoice_for(app, _sell(client, app, w, amount="250.00"))
    with app.app_context():
        from app.api_operations.commercial_sales import _serialize_invoice
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice

        out = _serialize_invoice(db_session.get(CommercialInvoice, inv["id"]))
        assert (out["total"], out["tax_included"], out["net_total"]) == ("250.00", "34.48", "215.52")


def test_no_total_or_price_changed_across_a_set_of_sales(app, client, seeded):
    """Sum of totals before == after: the invoice total equals the order total
    equals what was paid, for every sale in a mixed set."""
    w = _world(app)
    totals = []
    for amount, phones in (("250.00", None), ("300.00", 1), ("350.00", 2)):
        # one order exists per call, so read the newest instead of "the only"
        resp = _post_sale(client, app, w, "p1", amount=amount, phones=phones)
        assert resp.status_code == 200, resp.get_data(as_text=True)[:800]
        with app.app_context():
            from sqlalchemy import select

            from app.extensions import db_session
            from app.models.commercial_sales import SalesOrder

            order_id = db_session.execute(
                select(SalesOrder.id).order_by(SalesOrder.created_at.desc()).limit(1)
            ).scalar_one()
        _confirm(app, order_id, w["manager"])
        inv = _invoice_for(app, order_id)
        assert inv["total"] == inv["order_total"] == D(amount)
        totals.append(inv["total"] - 0)
    assert sum(totals) == D("900.00")
