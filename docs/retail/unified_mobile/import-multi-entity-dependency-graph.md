# Import Multi-Entity Dependency Graph (M5.8.12)

Real dependency graph (`ImportDependencyGraph.kt`), proven by
`ImportDependencyGraphTest.kt` (4/4,
`TEST-com.actionaura.retail.importing.entity.ImportDependencyGraphTest.xml`
tests="4" failures="0" errors="0").

## Real commit order (ported, not invented)

```
categories -> suppliers -> branches -> customers -> products
```

Directly ported from the legacy authority's own real, confirmed
`_ENTITY_ORDER` (`import-handler-matrix.md`'s own audit) — not
re-derived from first principles. Proven by
`commitOrderMatchesTheRealAuditedLegacyEntityOrder`.

## Real, minimal dependency set

Only `products` has real declared dependencies:

- `products -> categories` (optional — `category_id`; the real legacy
  handler auto-creates a missing Category by name rather than requiring
  it to exist first, so this is genuinely optional, never a hard block)
- `products -> branches` (optional — inventory row targets one Branch;
  the real legacy handler auto-creates a `"Main Store"` Branch if none
  exists)

Proven by `onlyProductsHasRealDeclaredDependencies` and
`productDependenciesOnCategoryAndBranchAreBothOptional`. No other
entity in this real audit has any cross-entity dependency —
`customers`'s position in the commit order is conventional, not
FK-driven (`import-handler-matrix.md`'s own explicit note).

## Real, deterministic sort for a partial entity selection

`sortByCommitOrder` orders whatever subset of the 5 entities a real
multi-entity import selects into the real commit order — proven by
`entitiesSelectedOutOfOrderAreSortedIntoTheRealCommitOrder` (Products,
Categories, Customers selected out of order → sorted to
Categories, Customers, Products).

## Real, explicit non-goal: no silent placeholder creation

This milestone's real dependency graph is deliberately narrower than
the legacy authority's own auto-creation behavior for Category/Branch —
M5.8.12's own checkpoint instruction ("Do not silently create
placeholder Categories, branches, Suppliers, or Products unless an
explicit approved rule exists") is satisfied here by keeping both real
Product dependencies OPTIONAL rather than by silently replicating the
legacy auto-creation behavior inside this dependency-graph module
itself — the real auto-creation decision (if adopted at all) belongs to
M5.8.15's commit-stage implementation, as an explicit, separately
documented rule, not folded silently into dependency-graph metadata.
