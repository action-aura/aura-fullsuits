"""phase9_5a_one_subscription_per_sales_order

Revision ID: 5de3f36f4c21
Revises: 86e9229f85c1
Create Date: 2026-08-13 16:30:00.000000

Real bug found by E-W0.3's first real CI run against real concurrent
Postgres load: test_item3_concurrent_fulfillment_requests_only_one_creates_subscription
(owner/tests/test_phase9_5d_fulfillment.py) got 4 Subscriptions from 5
concurrent fulfill_order() calls for the same SalesOrder, not 1.

Root cause: fulfill_order()'s SELECT ... FOR UPDATE lock on the order row is
released the instant create_subscription() commits internally (a deliberate,
documented tradeoff -- fulfill_order() can't wrap the whole multi-step
sequence in one outer transaction without changing create_subscription()/
create_license()'s own established commit-per-step behavior, needed for
crash recovery after a partial failure). order.status only flips to
FULFILLED in fulfill_order()'s own final commit, several steps later --
so a second, third, etc. concurrent caller can acquire the now-released
lock, still see the order as not-yet-FULFILLED, and also proceed to create
a Subscription, before the first caller's status write is ever committed.

This constraint is the real backstop: sales_order_id stays nullable
(subscriptions created outside fulfillment, e.g. direct/manual issuance,
never set it -- Postgres UNIQUE allows any number of NULLs), but no two
Subscriptions can ever share the same non-null sales_order_id. Paired with
app/commercial_sales/fulfillment.py catching the resulting IntegrityError
and re-querying for the winner's row (the same reuse path already used for
crash-recovery retries), this makes "exactly one Subscription per fulfilled
order" a real database guarantee instead of relying solely on lock timing
across multiple internally-committing service calls.
"""
from alembic import op


revision = '5de3f36f4c21'
down_revision = '86e9229f85c1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_subscriptions_one_per_sales_order", "owner_subscriptions", ["sales_order_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_subscriptions_one_per_sales_order", "owner_subscriptions", type_="unique")
