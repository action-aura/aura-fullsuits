# Phase 8V-P9 — Retail 88.00 Final Evidence

## Result: NOT APPLICABLE TO CURRENT ANDROID PRODUCT CONTRACT (Branch B, see
`retail-discount-contract-decision.md`). Backend PASS retained as mandatory and reconfirmed real.

## Backend evidence (real, reconfirmed this session)

```
$ .venv/Scripts/python.exe -m pytest products/retail/tests/retail_financial_authority_test.py -q -k worked_example
.                                                                        [100%]
1 passed, 9 deselected in 2.76s
```

`test_worked_example_subtotal_100_discount_20pct_tax_10pct`: real assertion that a real
`POST /api/sub/retail/sales` request with `discount_pct: 20` against a $100.00 line produces
`discount_amount=20.00, tax_amount=8.00, total=88.00` -- server-authoritative, Decimal-based, the
exact case this whole multi-session effort has been trying to reach at the UI layer.

## Why no physical Android UI evidence is claimed

Retail's Android POS UI has no discount-entry field (confirmed from source across three independent
audit generations, see `canonical-discount-and-export-contract.md`). This is not a workaround-avoided
gap -- it is a real, documented, never-promised absence. No UI screenshot, no fabricated tap sequence,
no unauthenticated raw API call is substituted as evidence of a UI-level PASS.

## Disposition

**NOT APPLICABLE.** Retained real return-integrity PASS (Phase 8V-P7, `RET-000001-5eba23a1`) stands
unaffected -- that scenario never depended on discount entry.
