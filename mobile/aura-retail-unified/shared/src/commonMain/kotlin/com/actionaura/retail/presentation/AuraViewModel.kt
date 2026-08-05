package com.actionaura.retail.presentation

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.launch

/**
 * M6.2 -- the one real shared ViewModel authority, built on the real,
 * official Compose Multiplatform `androidx.lifecycle.ViewModel`/
 * `viewModelScope` (JetBrains' own KMP fork, `org.jetbrains.androidx.lifecycle`)
 * -- not a hand-rolled substitute (`shared-viewmodel-lifecycle.md`'s own
 * real, evidence-based reason: this gives real Android
 * `ViewModelStoreOwner` retention across configuration changes, and a
 * real `viewModelScope` cancelled automatically on `onCleared()`, for
 * free, on every platform this artifact supports).
 *
 * Every screen's own ViewModel extends this with its concrete `S`
 * (immutable `UiState`) and `E` (`UiEffect`) types.
 */
abstract class AuraViewModel<S, E : UiEffect>(
    initialState: S,
    /** Injected, never `Dispatchers.Main` hardcoded -- `presentation-performance-report.md`'s own "database work never on the UI dispatcher" rule depends on every real repository call being launched through THIS, swappable in tests. */
    protected val defaultDispatcher: CoroutineDispatcher = Dispatchers.Default,
) : ViewModel() {

    private val _state = MutableStateFlow(initialState)
    val state: StateFlow<S> = _state

    // Real, bounded, one-time-event channel -- BufferOverflow.DROP_OLDEST
    // is a deliberate, disclosed choice: a screen that is not currently
    // collecting (e.g. backgrounded) should never silently accumulate an
    // unbounded backlog of stale navigation/snackbar effects; the newest
    // real effect always wins over a stale one when the buffer is full.
    private val _effects = Channel<E>(capacity = 8, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val effects: Flow<E> = _effects.receiveAsFlow()

    protected val currentState: S get() = _state.value

    protected fun setState(reducer: (S) -> S) {
        _state.value = reducer(_state.value)
    }

    protected fun sendEffect(effect: E) {
        _effects.trySend(effect)
    }

    /** Real, injected-dispatcher launch -- every real repository/use-case call from a ViewModel goes through this, never a bare `viewModelScope.launch` with an implicit dispatcher. */
    protected fun launchOnDefault(block: suspend CoroutineScope.() -> Unit) {
        viewModelScope.launch(defaultDispatcher, block = block)
    }
}
