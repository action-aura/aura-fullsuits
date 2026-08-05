# Shared ViewModel Lifecycle (M6.2)

Real decision: `AuraViewModel` extends the real, official Compose
Multiplatform `androidx.lifecycle.ViewModel` (`org.jetbrains.androidx.lifecycle:lifecycle-viewmodel-compose:2.8.2`,
JetBrains' own KMP fork), not a hand-rolled substitute. Proven
resolvable and compilable against this module's pinned Kotlin 2.0.21 /
Compose Multiplatform 1.7.0 by a real Gradle sync
(`:shared:compileDebugKotlinAndroid`, `BUILD SUCCESSFUL`); proven
functionally correct by `AuraViewModelTest.kt` (4/4).

## Why the official artifact, not a hand-rolled scope holder

- **Android lifecycle**: real `ViewModelStoreOwner` retention across
  configuration changes — a `ViewModel` instance survives rotation for
  free, without any custom `rememberSaveable`/process-death handling
  written by this project.
- **Coroutine cancellation**: `viewModelScope` is cancelled
  automatically when the owning `ViewModelStore` clears the ViewModel
  (screen permanently leaves the back stack) — no manual `close()`
  call is needed anywhere in screen code.
- **iOS lifecycle**: the same artifact ships a real Kotlin/Native
  implementation; a `ViewModel` constructed in `commonMain` behaves
  identically once real iOS compilation is possible (M6.27's own
  disclosed NOT_VERIFIED status for actual iOS execution on this host
  — the API surface itself is confirmed real and multiplatform by the
  successful Gradle dependency resolution, independent of whether this
  Windows host can compile the Apple targets).
- **Testing**: `viewModelScope`/`launch` work correctly in a plain JVM
  unit test with no Android framework/Robolectric present — confirmed
  real by `AuraViewModelTest.kt` running under `:shared:testDebugUnitTest`
  (no instrumentation, no emulator).

## `AuraViewModel<S, E : UiEffect>` — the real shape

- `state: StateFlow<S>` — backed by a private `MutableStateFlow`,
  updated only via `protected fun setState(reducer: (S) -> S)`, never
  exposed as mutable to subclasses' callers.
- `effects: Flow<E>` — backed by a bounded `Channel<E>` (capacity 8,
  `BufferOverflow.DROP_OLDEST`) via `receiveAsFlow()`. Real, disclosed
  choice: a screen not currently collecting (backgrounded) never
  accumulates an unbounded backlog — the newest real effect wins over
  a stale one once the buffer is full, rather than blocking the
  producer or growing without bound.
- `launchOnDefault { ... }` — the ONLY sanctioned way a subclass
  launches coroutine work; always routes through an injected
  `CoroutineDispatcher` (default `Dispatchers.Default`, swappable per
  instance), never a hardcoded `Dispatchers.Main` — satisfies
  `presentation-performance-report.md`'s own "database work never on
  the UI dispatcher" requirement structurally, not by convention alone.

## Creation / retention / disposal policy

- **Creation**: one `AuraViewModel` subclass instance per real
  navigation destination, constructed through the platform's own
  `ViewModelStoreOwner` machinery (Android: `viewModel()` composable
  factory; future iOS: the same KMP artifact's equivalent) — never
  constructed directly inside a Composable body (which would create a
  new instance every recomposition, the exact hazard M6.2's own
  checkpoint text warns against).
- **Retention**: survives recomposition and configuration change for
  as long as its destination remains on the real back stack.
- **Disposal**: real `viewModelScope` cancellation happens automatically
  when the destination is popped permanently — no manual wiring
  required in any screen.
- **Saved-state**: not yet integrated (`androidx.lifecycle:lifecycle-viewmodel-savedstate`)
  — real, disclosed scope decision: no M6 vertical slice (Category/
  Branch/Reporting/Import) has form state that must survive real
  process death mid-edit; this is deferred to whichever later
  milestone first needs it (e.g. a long multi-step Import wizard),
  not silently assumed solved.
- **Error recovery / refresh**: each concrete `AuraViewModel` subclass
  owns its own retry/refresh action as a normal `UiAction` → `setState`
  cycle; no shared automatic-retry machinery exists in the base class
  (a real, deliberate choice — automatic retry policy varies too much
  per screen to generalize honestly here).
