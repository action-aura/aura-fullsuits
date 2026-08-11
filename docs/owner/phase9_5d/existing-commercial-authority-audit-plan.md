# Phase 9.5D — Existing Commercial Authority Audit: Plan

## Method

A research agent is auditing the real repository (not assumptions from memory) against the 35-item checklist the governing spec provides (Product, Platform, Plan, PlanPriceHistory, AddOn, EntitlementDefinition, Subscription+lifecycle, renewal requests, Payment records, License, Installation, CommercialOperation, Pilot, EmergencyExtension, DeviceException, Employee, Customer, Lead, Audit, Idempotency, Approval, numbering, currency, tax fields, discount fields, commission foundation, Quote foundation, Order foundation, Invoice foundation, Refund foundation, `commercial_ops` module, RBAC registry, stable service-error pattern, OpenAPI, preflight), classifying each into one of: FULLY IMPLEMENTED AND REUSABLE / FOUNDATION ONLY / MODEL PRESENT SERVICE MISSING / SERVICE PRESENT UI-API MISSING / REQUIRES SAFE EXTENSION / REQUIRES NEW AUTHORITY / DUPLICATION RISK / OUT OF SCOPE.

## Why an agent, not direct reads

The checklist spans ~35 distinct capabilities across a large existing codebase (`owner/app/models/`, `owner/app/commercial_sales/`, `owner/app/commissions/`, `owner/app/commercial_ops/`, `owner/app/licensing/`, `owner/app/catalog/`, migrations). Reading every relevant file directly would consume a large fraction of this session's context before any 9.5D code is written. A dedicated research pass, reporting back file:line-referenced findings, keeps this budget for the actual implementation milestones.

## Output

`existing-commercial-authority-audit.md` (the raw findings, organized by the spec's 35 items) and `commercial-authority-reuse-matrix.md` (the same findings collapsed into the classification table, driving every subsequent milestone's build-vs-reuse decision) and `commercial-duplication-risk-report.md` (anything flagged DUPLICATION RISK, with a resolution decision for each).

## Non-negotiable constraint this audit enforces

Per the spec: do not create a second Product/Plan/pricing/Subscription/Payment/License/Customer/employee-ownership/audit/idempotency authority. Every later milestone's design must cite this audit before introducing a new model or service.
