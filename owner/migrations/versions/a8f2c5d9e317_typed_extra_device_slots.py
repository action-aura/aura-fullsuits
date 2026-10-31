"""typed_extra_device_slots (owner decision 2026-10-31: extra PHONES and extra DESKTOPS, strictly typed)

Revision ID: a8f2c5d9e317
Revises: d7e3a1b94c52
Create Date: 2026-10-31 23:00:00.000000

The owner's final rule: a package includes 2 devices of ANY kind; an extra phone
(50 JOD once) lets one MORE phone in, an extra desktop (50 JOD once) lets one MORE
desktop in -- never the other way round. The old EXTRA_DEVICE_PAIR (1 desktop + 1
phone for one price) is retired; the licence now has to remember WHICH kind was
bought, because device_limit alone cannot tell "3 devices, one is a bought phone"
from "3 devices, one is a bought desktop".

  owner_licenses.extra_desktop_slots   INTEGER NOT NULL DEFAULT 0, CHECK >= 0
  owner_licenses.extra_phone_slots     INTEGER NOT NULL DEFAULT 0, CHECK >= 0
  owner_license_scheduled_stops.desktops_removed / phones_removed   INTEGER NULL
      (devices_removed stays the TOTAL, so the existing CHECK and readers are unchanged)

device_limit stays the total allowance everything already reads. These two columns
only say how many of the extras are desktop-only and phone-only.

BACKFILL (plain SQL, idempotent -- an absolute recomputation, so a re-run gives the same
answer): each paid EXTRA_DEVICE_PAIR unit becomes one extra desktop + one extra phone.
pairs bought (the subscription's order lines) minus pairs already taken away by an
applied, uncancelled refund stop (devices_removed / 2), never above what device_limit
actually holds beyond the package's included devices ((device_limit - included) / 2),
never below 0. A single untyped EXTRA_DEVICE or a staff "add devices" leaves the rest
of device_limit as untyped, any-kind capacity -- exactly as it behaved before.
Every refund stop that removes devices (waiting, applied, cancelled) gets
desktops_removed = phones_removed = devices_removed / 2 (rounded down; an odd
remainder stays untyped): a waiting one takes the same pair away when it fires, and
an applied one keeps counting as the pair it was, so a later refund of the same
invoice does not take that pair off a second time.

Safe with the old code running: two NOT NULL columns with a server default that nothing
old reads, and two nullable columns. DOWNGRADE drops the four columns; the typed
information is lost, device_limit (the total) is kept.

a8f2c5d9e317 is an Alembic revision id, not a credential.
"""
from alembic import op
import sqlalchemy as sa


revision = 'a8f2c5d9e317'  # pragma: allowlist secret -- an Alembic revision id, not a credential
down_revision = 'd7e3a1b94c52'  # pragma: allowlist secret -- an Alembic revision id, not a credential
branch_labels = None
depends_on = None

LICENCES = 'owner_licenses'
STOPS = 'owner_license_scheduled_stops'

_BACKFILL_LICENCES = """
WITH pairs AS (
    SELECT s.id AS subscription_id, SUM(l.quantity) AS pairs
    FROM owner_subscriptions s
    JOIN owner_sales_order_lines l ON l.sales_order_id = s.sales_order_id
    JOIN owner_addons a ON a.id = l.addon_id AND a.addon_code = 'EXTRA_DEVICE_PAIR'
    GROUP BY s.id
),
removed AS (
    SELECT st.license_id, SUM(st.devices_removed) AS devices
    FROM owner_license_scheduled_stops st
    WHERE st.applied_at IS NOT NULL AND st.cancelled_at IS NULL AND st.devices_removed IS NOT NULL
    GROUP BY st.license_id
),
typed AS (
    SELECT lic.id AS license_id,
           GREATEST(0, LEAST(
               COALESCE(p.pairs, 0) - COALESCE(r.devices, 0) / 2,
               (lic.device_limit - pl.included_device_count) / 2
           )) AS pairs_left
    FROM owner_licenses lic
    JOIN owner_plans pl ON pl.id = lic.plan_id
    LEFT JOIN pairs p ON p.subscription_id = lic.subscription_id
    LEFT JOIN removed r ON r.license_id = lic.id
)
UPDATE owner_licenses
SET extra_desktop_slots = typed.pairs_left, extra_phone_slots = typed.pairs_left
FROM typed
WHERE typed.license_id = owner_licenses.id AND typed.pairs_left > 0
"""

_BACKFILL_STOPS = """
UPDATE owner_license_scheduled_stops
SET desktops_removed = devices_removed / 2, phones_removed = devices_removed / 2
WHERE devices_removed IS NOT NULL
"""


def upgrade() -> None:
    op.add_column(LICENCES, sa.Column('extra_desktop_slots', sa.Integer(), server_default='0', nullable=False))
    op.add_column(LICENCES, sa.Column('extra_phone_slots', sa.Integer(), server_default='0', nullable=False))
    op.create_check_constraint('ck_owner_licenses_extra_desktop_slots_nonneg', LICENCES, 'extra_desktop_slots >= 0')
    op.create_check_constraint('ck_owner_licenses_extra_phone_slots_nonneg', LICENCES, 'extra_phone_slots >= 0')
    op.add_column(STOPS, sa.Column('desktops_removed', sa.Integer(), nullable=True))
    op.add_column(STOPS, sa.Column('phones_removed', sa.Integer(), nullable=True))
    bind = op.get_bind()
    bind.execute(sa.text(_BACKFILL_LICENCES))
    bind.execute(sa.text(_BACKFILL_STOPS))


def downgrade() -> None:
    op.drop_column(STOPS, 'phones_removed')
    op.drop_column(STOPS, 'desktops_removed')
    op.drop_constraint('ck_owner_licenses_extra_phone_slots_nonneg', LICENCES, type_='check')
    op.drop_constraint('ck_owner_licenses_extra_desktop_slots_nonneg', LICENCES, type_='check')
    op.drop_column(LICENCES, 'extra_phone_slots')
    op.drop_column(LICENCES, 'extra_desktop_slots')
