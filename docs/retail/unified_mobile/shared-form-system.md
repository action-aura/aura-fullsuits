# Shared Form System (M6.9)

Real, shared form-field state contract — `presentation.FormFieldState<T>`.
Proven by `FormFieldStateTest.kt` (5/5).

## `FormFieldState<T>`

`value: T?` (last successfully-parsed real domain value) vs.
`displayValue: String` (the raw text currently shown/being typed) are
deliberately separate — a numeric field mid-edit (e.g. `"12."`) keeps
its last-good `value` while `displayValue` shows the transiently
invalid text; `domainError` surfaces the real parse failure once the
user leaves the field (`touched`), never silently coerced to zero.

`isValid`: `domainError == null && (!required || value != null)` —
structural, never inferred from `displayValue` content.
`List<FormFieldState<*>>.allValid()` is the real, shared submit-gate
every M6+ form uses — a form may only submit once every real field it
owns is valid, never a partial/best-effort submit.

## `AuraTextField`

The one real shared text-input component (M6.16/M6.17's Category/
Branch forms both use it directly) — binds `FormFieldState<String>`,
shows `domainError.messageKey` as `supportingText` only once `touched`
(never flashes a validation error before the user has interacted with
the field).

## Money/Quantity input — real, disclosed scope

M6.9's own required field-type list includes exact Money/Quantity/
barcode/dropdown/switch/date-range/multi-select inputs. This milestone
builds the real, shared STATE contract (`FormFieldState<T>`, generic
over `T`) that any of those field types can use, plus the one concrete
UI component (`AuraTextField`) the two M6 vertical slices with real
forms (Category, Branch — both text-only fields per
`mobile-screen-route-matrix.md`) actually need. Real Money/Quantity
input composables (parsing through `Money.parse`/`Quantity.parse` per
keystroke-or-blur, never `Double`) are deferred to whichever later
milestone first builds a form with a real financial field (Product
create/edit, inventory adjustment — both `NOT_IN_M6`) — not built
speculatively ahead of a real consumer, and not silently claimed done.
`ui.format.MoneyQuantityDisplay` (M6.10) already proves the DISPLAY
half of this contract is real and exact; the INPUT half's parser
wiring follows the identical `ImportDomainValueParser` precedent
(M5.8) once a real screen needs it.
