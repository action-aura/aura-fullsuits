# Phase 9.5A — Commission Calculation Contract

## Extensible but bounded rule model (per the spec's own instruction — not every rule type is built)

`rule_type` supported this phase: `PERCENTAGE_OF_PAYMENT` (rate applied to a confirmed payment amount),
`FIXED_AMOUNT` (flat amount per qualifying invoice/payment). `PERCENTAGE_FIRST_SALE` and
`PERCENTAGE_RENEWAL` are reserved column/enum values (schema supports them) but their real
distinguishing logic (first sale vs. renewal detection against the customer's `Subscription` history)
is **not implemented this phase** — calling `CommissionEligibilityService` with a rule of that type
raises a clear `NotImplementedError` in the service layer (fails loudly, never silently falls back to
treating a renewal as a first sale) until a later phase adds it.

## Calculation is never derived only at display time

`commission_amount` is computed once, at the moment eligibility is evaluated (payment confirmation
time), and stored on the `commission_ledger_entries` row — a later change to `rate_percentage` in a new
`commission_rule_versions` row never recomputes an already-created entry (the entry's
`commission_rule_version_id` pins the exact rate that was actually applied). A "commission preview"
(Milestone 22's proving service) computes the same real formula against a hypothetical amount without
writing a ledger row — used for showing an employee "if this deal closes, you'd earn ~X" before it
actually happens, clearly distinct from a posted entry.

## Formula (real, `Decimal`-exact)

```python
def calculate_commission(rule: CommissionRuleVersion, base_amount: Decimal) -> Decimal:
    if rule.rule_type == "PERCENTAGE_OF_PAYMENT":
        return (base_amount * rule.rate_percentage / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if rule.rule_type == "FIXED_AMOUNT":
        return rule.fixed_amount
    raise NotImplementedError(f"rule_type {rule.rule_type} not implemented this phase")
```

`ROUND_HALF_UP` matches this codebase's own existing convention for money rounding elsewhere
(commercial/financial code in Retail/Clinic already uses this rounding mode — reused for consistency,
not reinvented).
