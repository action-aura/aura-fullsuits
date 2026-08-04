# Aura Retail Unified Mobile — Financial Error Code Map (M3.0)

The current Python authority (`retail_api.py`'s `create_sale`/`create_return`) does not use stable machine-readable error codes at all — every failure is an HTTP status + a free-text English `message` string (e.g. `{'status': 'error', 'message': 'Insufficient stock for "Widget" (have 3, requested 5).'}`). There is no existing canonical error-code authority to reuse for the financial core specifically (unlike, say, Aura Owner's `StableCodeError` pattern in the sibling Owner codebase, which this Retail Python backend does not use). The shared Kotlin core introduces stable codes for the first time — a `CANONICAL_UNIFIED_RULES` addition, not a port, since there is nothing to port.

| Kotlin stable code | Real Python condition it replaces | Python message example | Trigger |
|---|---|---|---|
| `INVALID_QUANTITY` | `float(item.get('quantity'))` raises `TypeError`/`ValueError`, or (closed gap) the parsed value is NaN/Infinite | "Invalid quantity." | Unparseable, NaN, or infinite quantity string |
| `NON_POSITIVE_QUANTITY` | `qty <= 0` | "Quantity must be greater than zero." | Zero or negative quantity |
| `INVALID_PRICE` | (new — Python resolves price from the trusted product row, never parses client-submitted price at all) | n/a | A product's resolved price is non-finite/negative — should never happen if the catalog is valid, but the engine validates its own inputs regardless of source |
| `NEGATIVE_PRICE` | (new, same reasoning as above) | n/a | Resolved unit price < 0 |
| `DISCOUNT_EXCEEDS_LIMIT` | Does not exist — Python clamps, never rejects (`clamp_discount_pct`) | n/a | **Not raised by the ported `calculate_line` path** (clamping is the `LEGACY_PARITY` behavior); reserved for a future explicit "reject rather than clamp" mode if a canonical product decision ever wants one — not used by M3's default behavior |
| `PAYMENT_BELOW_TOTAL` | Does not exist as a hard reject — see invariant #13 in `financial-invariant-catalog.md`; underpayment is legal AR debt for an eligible customer | "Credit sales require a customer (walk-in not allowed)." (only when *no customer* + underpayment) | Reserved for the specific real-reject case: underpayment with no customer attached, or customer's credit mode is `none`, or (if `enforce_credit_limit == 'block'`) the credit limit is exceeded. **Not a blanket "any underpayment fails" code** — see `CREDIT_MODE_DISALLOWED` / `CREDIT_LIMIT_EXCEEDED` below for the precise split |
| `CREDIT_SALE_REQUIRES_CUSTOMER` | `create_sale`, "Credit sales require a customer (walk-in not allowed)." | (verbatim above) | Underpayment with no `customer_id` |
| `CREDIT_MODE_DISALLOWED` | `create_sale`, `credit_mode == 'none'` | "This customer is not allowed to buy on credit." | Underpayment, customer's credit mode forbids it |
| `CREDIT_LIMIT_EXCEEDED` | `create_sale`, blocking branch | "Credit limit exceeded. Limit {X}, outstanding {Y}, this sale adds {Z}." | Underpayment pushes the customer over their limit and `enforce_credit_limit == 'block'` |
| `RETURN_EXCEEDS_SOLD_QUANTITY` | `create_return`, product not part of the sale, or (see next) already fully returned | "Product {pid} was not part of sale {sale_id}." | Return references a product never sold on that sale |
| `RETURN_EXCEEDS_REMAINING_QUANTITY` | `create_return`, `qty > remaining + 0.0001` | "Cannot return {qty} of product {pid}: only {remaining} remain returnable..." | Cumulative-return limit exceeded |
| `REFUND_EXCEEDS_REFUNDABLE_AMOUNT` | Structurally implied by `RETURN_EXCEEDS_REMAINING_QUANTITY` (Python never computes a refund amount independent of a quantity check) | n/a | Reserved for a direct amount-based refund path if the product ever adds one; the current quantity-driven return flow never reaches an amount-only check |
| `INSUFFICIENT_STOCK` | `create_sale`, `qty > on_hand` | "Insufficient stock for \"{name}\" (have {X}, requested {Y})." | Sale line requests more than on-hand stock |
| `DUPLICATE_OPERATION_CONFLICT` | Does not exist — real gap (invariant #12) | n/a | **CANONICAL_UNIFIED**: same idempotency key, materially different payload |
| `NUMERIC_OVERFLOW` | Does not exist as an explicit check — Python `Decimal`/`float` would raise an uncaught `Exception` caught by the route's blanket `except Exception as e: ... 500` handler, an undifferentiated 500 rather than a real domain error | (blanket 500, undifferentiated) | **CANONICAL_UNIFIED**: the shared `Money`/`Quantity` types detect and reject overflow explicitly rather than letting an unhandled exception surface as a generic 500 |
| `PRODUCT_NOT_FOUND` | `create_sale`, product lookup miss | "Product {pid} not found." | — |
| `PRODUCT_INACTIVE` | `create_sale`, `status != 'active'` | "Product \"{name}\" is not available for sale." | — |
| `SALE_NOT_FOUND` | `create_return`, sale lookup miss | "Original sale not found." | — |
| `EMPTY_SALE` | `create_sale`, no items | "No items in sale." | — |
| `EMPTY_RETURN` | `create_return`, no items | "No items to return" | — |

## Disposition

Every code above is either a direct, faithful translation of a real, precise Python condition (`LEGACY_PARITY`), or an explicit, documented new/closed-gap addition (`CANONICAL_UNIFIED`, cross-referenced to its invariant-catalog entry). No code was invented without a real Python condition or a real, named gap behind it.
