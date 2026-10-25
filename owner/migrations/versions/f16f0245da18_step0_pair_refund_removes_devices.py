"""step0_pair_refund_removes_devices (Step 0 fix round, 2026-10-25)

Revision ID: f16f0245da18
Revises: 38a13f8b2f8e
Create Date: 2026-10-25 08:00:00.000000

A refund of only the extra-device (pair) money keeps the licence running
but must take those devices away -- after the same 2 days a refund's licence
stop waits. owner_license_scheduled_stops could only say "suspend at":
to_status NOT NULL with CHECK (to_status IN ('SUSPENDED')).

Changes owner_license_scheduled_stops only:
  devices_removed   integer, NULL     how many devices the row takes away
  to_status         NOT NULL -> NULL  (a device removal changes no status)
  ck_owner_license_scheduled_stops_to_status becomes
      (to_status IS NOT NULL AND to_status = 'SUSPENDED' AND devices_removed IS NULL)
      OR (to_status IS NULL AND devices_removed IS NOT NULL AND devices_removed > 0)
so every existing row (all SUSPENDED stops) is exactly as strict as before.

Safe with the old code running: it only ever writes SUSPENDED rows with no
devices_removed. The downgrade refuses while any device-removal row exists
(dropping the column would turn it into a row with no meaning); otherwise it
restores the NOT NULL and the old CHECK.

38a13f8b2f8e was named as this revision's down_revision in ROADMAP.md before
the migration was written (single-writer resource), commit 2ec20fce.
"""
from alembic import op
import sqlalchemy as sa


revision = 'f16f0245da18'
down_revision = '38a13f8b2f8e'
branch_labels = None
depends_on = None

_STOPS = "owner_license_scheduled_stops"
_CHECK = "ck_owner_license_scheduled_stops_to_status"


def upgrade() -> None:
    op.add_column(_STOPS, sa.Column("devices_removed", sa.Integer(), nullable=True))
    op.drop_constraint(_CHECK, _STOPS, type_="check")
    op.alter_column(_STOPS, "to_status", existing_type=sa.String(length=32), nullable=True)
    op.create_check_constraint(
        _CHECK, _STOPS,
        # Spelled with IS NOT NULL on purpose: a CHECK passes when it is NULL,
        # and "to_status IS NULL AND devices_removed > 0" is NULL -- not false --
        # for a row with neither (measured: the first spelling accepted it).
        "(to_status IS NOT NULL AND to_status = 'SUSPENDED' AND devices_removed IS NULL) "
        "OR (to_status IS NULL AND devices_removed IS NOT NULL AND devices_removed > 0)",
    )


def downgrade() -> None:
    bind = op.get_bind()
    removals = bind.execute(sa.text(f"SELECT count(*) FROM {_STOPS} WHERE devices_removed IS NOT NULL")).scalar_one()
    if removals:
        raise RuntimeError(
            f"Refusing to downgrade f16f0245da18: {removals} scheduled device removal(s) exist and the old schema "
            "cannot hold them. Apply or export them first."
        )
    op.drop_constraint(_CHECK, _STOPS, type_="check")
    op.alter_column(_STOPS, "to_status", existing_type=sa.String(length=32), nullable=False)
    op.create_check_constraint(_CHECK, _STOPS, "to_status IN ('SUSPENDED')")
    op.drop_column(_STOPS, "devices_removed")
