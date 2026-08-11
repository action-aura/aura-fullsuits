# Phase 9.5A — Scope and Boundaries

## Objective

Extend Aura Owner's data model, RBAC, service layer, and API contracts to support a unified internal
commercial-operations platform (employees, leads, customers, sales documents, commissions, expenses,
management notes, daily reporting, multi-device licensing policy) — foundation only.

## Explicitly forbidden this phase (unchanged from the governing instruction)

Real VPS deployment; public domain; remote production/staging; Phase 9R; Phase 9.5B+; complete Employee
Portal UI; complete Admin Portal UI; Flutter/Android/iOS app implementation; background employee
location tracking; payroll; full accounting ERP; payment-gateway integration; card/bank data storage;
e-invoicing; WhatsApp/SMS/external email; automatic client updates; public customer self-service;
Aura Core integration; unrelated Clinic/Retail features; changing Android/Windows licensing identities;
breaking Phase 8 contracts; real customer/employee data.

## What "foundation only" means concretely in this phase

- Real PostgreSQL migrations and real SQLAlchemy models — yes.
- Real minimal service-layer functions proving the architecture (create lead, assign, convert, resolve
  device policy, commission preview) — yes, narrowly scoped per Milestone 22's own explicit list.
- Real RBAC permission additions and seed updates — yes.
- Real OpenAPI-documented contracts for endpoints — yes, contracts; full route implementation only
  where explicitly listed as foundation-proving.
- Flask routes/blueprints wiring every contract end-to-end — **no**, out of scope (UI/full API
  implementation is Phase 9.5B+ territory per the governing instruction's own boundary).
- Full commission/invoice/payout business workflows — **no**, foundation models + minimal preview
  logic only.

## Reuse-first discipline (the actual hard part of this phase)

Milestone 1's audit (see `existing-owner-capability-audit.md`) found that Customer, Payment
(`PaymentRecord`), Subscription, License, Installation, DeviceSlotException, InternalNotification, and
the full audit/RBAC/scheduler infrastructure already exist and are real, tested, production-grade. This
phase's design discipline is almost entirely about NOT rebuilding any of those — every new model is
additive to what's missing (leads, quotes, commercial invoices distinct from raw payments, commissions,
expenses, employee profiles, presence, management notes, daily snapshots), never a parallel system.
