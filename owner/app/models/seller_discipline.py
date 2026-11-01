"""Seller agreement clauses 16, 17 and 18 -- sanctions and the end of the agreement.

THREE TABLES, nothing else stored:

  owner_sanction_cases        one row per sanction case (clause 16). The case row IS the flag:
                              a COMMISSION_WITHHOLD case that is DECIDED_APPLIED withholds the
                              commission of its deal, so there is no second "withhold" table to
                              drift from the case (commissions/ledger.py asks the case).
  owner_seller_terminations   one row per ending of a seller's agreement (clause 17): the seller's
                              or the company's 7-day notice, or an immediate one from a sanction.
  owner_seller_trial_credits  clause 18: a customer whose TRIAL started before the termination
                              keeps crediting the seller for its FIRST payment. One row per trial,
                              written when the termination takes effect, released once the trial
                              is over (paid or not).

NO NEW COLUMN on customers, subscriptions or the commission ledger: ownership is the customer's
existing assigned_sales_staff_id, and the money is untouched.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Identity, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin

SANCTION_KINDS = ("WARNING", "SUSPENSION", "COMMISSION_WITHHOLD", "TERMINATION")
SANCTION_STATUSES = ("NOTICE_SENT", "RESPONDED", "DECIDED_APPLIED", "DECIDED_DROPPED")
# Clause 16: the company may end the agreement AT ONCE, with no notice and no reply period, for
# these three. Any other category goes through the 2-working-day reply first.
IMMEDIATE_CATEGORIES = ("MONEY_MANIPULATION", "CONFIDENTIAL_DISCLOSURE", "REVERSE_ENGINEERING")
SANCTION_CATEGORIES = IMMEDIATE_CATEGORIES + ("OTHER",)
SANCTION_LINK_KINDS = ("SALES_ORDER", "PAYMENT", "CUSTOMER", "CASH_HAND_IN")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class SanctionCase(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_sanction_cases"
    __table_args__ = (
        CheckConstraint(_in("kind", SANCTION_KINDS), name="ck_owner_sanction_cases_kind"),
        CheckConstraint(_in("status", SANCTION_STATUSES), name="ck_owner_sanction_cases_status"),
        CheckConstraint(_in("reason_category", SANCTION_CATEGORIES), name="ck_owner_sanction_cases_category"),
        CheckConstraint("length(btrim(reason_text)) > 0", name="ck_owner_sanction_cases_reason_text"),
        CheckConstraint(
            "kind <> 'SUSPENSION' OR suspension_until IS NOT NULL", name="ck_owner_sanction_cases_suspension_date"),
        CheckConstraint(
            "kind <> 'COMMISSION_WITHHOLD' OR withheld_sales_order_id IS NOT NULL",
            name="ck_owner_sanction_cases_withhold_order"),
        # A decided case has a decision time, an undecided one does not.
        CheckConstraint(
            "(status IN ('DECIDED_APPLIED', 'DECIDED_DROPPED')) = (decided_at IS NOT NULL)",
            name="ck_owner_sanction_cases_decided_time"),
        # Only an immediate case has no reply deadline.
        CheckConstraint("immediate OR response_due_at IS NOT NULL", name="ck_owner_sanction_cases_deadline"),
        CheckConstraint(
            "NOT immediate OR (kind = 'TERMINATION' AND status = 'DECIDED_APPLIED')", name="ck_owner_sanction_cases_immediate"),
        Index("ix_owner_sanction_cases_seller_status", "seller_staff_user_id", "status"),
        Index("ix_owner_sanction_cases_withheld_order", "withheld_sales_order_id"),
    )

    # The number people say out loud ("withheld, case 7"). Never used in a URL: the id is.
    case_number: Mapped[int] = mapped_column(Integer, Identity(always=False), nullable=False, unique=True)
    seller_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False, index=True)
    opened_by_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_category: Mapped[str] = mapped_column(String(32), nullable=False, default="OTHER", server_default="OTHER")
    reason_text: Mapped[str] = mapped_column(Text, nullable=False)
    # An optional pointer at what the case is about (a sale, a payment, a cash hand-in, a customer).
    link_kind: Mapped[str | None] = mapped_column(String(16))
    link_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    suspension_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # COMMISSION_WITHHOLD: the deal (sales order) whose commission is held back.
    withheld_sales_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_sales_orders.id"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="NOTICE_SENT", server_default="NOTICE_SENT")
    immediate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    notice_sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # The end of the 2nd Jordan working day after the notice (commissions/statements.reply_due_at).
    response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_text: Mapped[str | None] = mapped_column(Text)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"))
    decision_note: Mapped[str | None] = mapped_column(Text)
    # A SUSPENSION that has ended (the date passed and the account was re-opened).
    suspension_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SellerTermination(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_seller_terminations"
    __table_args__ = (
        CheckConstraint("status IN ('NOTICE', 'APPLIED')", name="ck_owner_seller_terminations_status"),
        CheckConstraint("effective_at >= notice_given_at", name="ck_owner_seller_terminations_dates"),
        CheckConstraint("(status = 'APPLIED') = (applied_at IS NOT NULL)", name="ck_owner_seller_terminations_applied_time"),
        # One notice running per seller; a second "End" while one runs is refused by the database too.
        Index("uq_owner_seller_terminations_one_notice", "seller_staff_user_id", unique=True,
              postgresql_where=text("status = 'NOTICE'")),
        Index("ix_owner_seller_terminations_status_effective", "status", "effective_at"),
    )

    seller_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False, index=True)
    initiated_by_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False)
    initiated_by_seller: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sanction_case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_sanction_cases.id"))
    reason: Mapped[str | None] = mapped_column(Text)
    notice_given_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # A manager may choose another seller to receive the customers that return to the company.
    reassign_to_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"))
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="NOTICE", server_default="NOTICE")
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SellerTrialCredit(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_seller_trial_credits"
    __table_args__ = (
        CheckConstraint("release_reason IS NULL OR release_reason IN ('CONVERTED', 'TRIAL_ENDED')",
                        name="ck_owner_seller_trial_credits_reason"),
        CheckConstraint("(released_at IS NULL) = (release_reason IS NULL)", name="ck_owner_seller_trial_credits_released"),
    )

    termination_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_seller_terminations.id"), nullable=False)
    seller_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=False)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_subscriptions.id"), nullable=False, unique=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(String(16))
