# Target chart of accounts (Aura Retail)

One page that says which account codes exist, which are planned, and the rules
that stop the chart drifting. The code of record is `DEFAULT_CHART` in
`products/retail/backend/core/retail/general_ledger.py`; this file is the plan
around it. If the two disagree, the code is what shipped and this file is stale.

## Rules

a. **Frozen once released.** A code's name, account type and normal balance
   never change after a build that seeds it has shipped. `ensure_default_chart`
   uses `INSERT OR IGNORE`, so it never overwrites a seeded row; a rename needs
   an explicit migration `UPDATE`, and a type change is forbidden once anything
   has posted to the account (every report places an account by its type).
b. **New codes are flat (no parent).** The one legacy exception is 4000 Sales
   Revenue, which is the parent of 4900 Sales Returns and Allowances *and* is
   posted to directly by `post_sale` (C1). Do not copy that shape: a parent that
   is also posted to makes roll-ups ambiguous.
c. **Seed only what the code posts.** A code goes into `DEFAULT_CHART` in the
   wave that first posts to it, not before. Reserved ranges below are listed so
   nobody invents a clashing code; they are not seeded.
d. **Arabic names need a native reader.** The Arabic column below is a proposal;
   a native speaker signs it off before it is shown anywhere. Account names are
   displayed raw today (the English seed name), so the Arabic is not yet visible
   (STOCK-BOOKS residual R6).
e. **Statements are type-driven.** Balance sheet and income statement place an
   account by its type, and only `5900` is cost of goods sold. A new expense
   code lands under operating expenses automatically.

## Seeded today

| Code | English name | Type | Normal | Parent | Arabic (proposed) | Seeded by |
|------|--------------|------|--------|--------|-------------------|-----------|
| 1000 | Cash and Bank | asset | debit | - | - | v38 |
| 1100 | Accounts Receivable | asset | debit | - | - | v38 |
| 1200 | Inventory | asset | debit | - | - | v38 |
| 2000 | Accounts Payable | liability | credit | - | - | v38 |
| 2100 | Tax Payable | liability | credit | - | - | v38 |
| 3000 | Owner's Equity | equity | credit | - | - | v38 |
| 4000 | Sales Revenue | revenue | credit | - | - | v38 |
| 4900 | Sales Returns and Allowances | revenue | debit | 4000 | - | v38 |
| 5000 | Loyalty Program Expense | expense | debit | - | - | v38 |
| 5900 | Cost of Goods Sold | expense | debit | - | - | v38 |
| 1250 | Stock in Transit | asset | debit | - | بضاعة في الطريق | STOCK-BOOKS |
| 1260 | Goods Ordered Not Received | asset | debit | - | بضاعة مطلوبة لم تُستلم | STOCK-BOOKS |
| 3900 | Opening Balance Equity | equity | credit | - | حقوق ملكية الأرصدة الافتتاحية | STOCK-BOOKS |
| 5910 | Shrinkage and Adjustments | expense | debit | - | العجز وتسويات المخزون | STOCK-BOOKS |

Notes on the STOCK-BOOKS rows:

* 1260 holds a purchase order's value from the order until the goods arrive; the
  receipt moves it to 1200. Accounts Payable is still recognised at order time
  (interim; moving it to receipt belongs with W4a).
* 1250 holds stock sent between branches and not yet received. A shortage on
  receipt is written off to 5910, an over-receipt credits it.
* 3900 is the offset for stock entered at product create/import. It is NOT the
  `opening_balance` journal source type, which belongs to the v38 AR/AP backfill.
* 5910 is its own line *below* gross profit. 5900 stays the only cost of goods
  sold, so a stock count never moves gross margin.

## Cheques (MONEY-3 M3, retail v51)

| Code | English name | Type | Normal | Arabic (proposed) | Wave |
|------|--------------|------|--------|-------------------|------|
| 1150 | Cheques Under Collection | asset | debit | شيكات برسم التحصيل | MONEY-3 |
| 2050 | Cheques Payable | liability | credit | شيكات مستحقة الدفع | MONEY-3 |

* 1150 holds a customer cheque from the day it is received until it reaches the bank
  (clear, or endorsed away); a bounce or cancel before that puts it back out against AR.
* 2050 holds a supplier cheque from the day it is issued until it clears.
* "Bank" is 1000 Cash and Bank (one account; 1010-1080 stay reserved). A cheque that bounces
  AFTER clearing reverses the bank leg instead of the holding account.
* Cheques alive at the v51 upgrade each get one opening journal (memo "cheque in flight before
  v51" / "cheque cleared before v51"); dead cheques get nothing.

## Planned, not seeded yet

| Code | English name (working) | Type | Normal | Arabic (proposed) | Wave |
|------|------------------------|------|--------|-------------------|------|
| 5950 | Cash Over and Short | expense | debit | العجز والزيادة في النقدية | shift/cash drawer |

## Reserved ranges (codes held, names not frozen)

| Range | Intended use |
|-------|--------------|
| 1010-1080 | Cash and bank sub-accounts (drawer, bank, card clearing, wallets), if 1000 is ever split |
| 6000-6900 | Operating expense categories |

Nothing in a reserved range is seeded or named until a wave posts to it; the
working names above are not frozen.
