package com.actionaura.retail.presentation

import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

data class CounterState(val count: Int = 0, val loadState: LoadState = LoadState.Idle)
data class CounterEffect(val message: String) : UiEffect

class CounterViewModel(dispatcher: kotlinx.coroutines.CoroutineDispatcher) : AuraViewModel<CounterState, CounterEffect>(CounterState(), dispatcher) {
    fun increment() = launchOnDefault {
        setState { it.copy(count = it.count + 1) }
    }

    fun incrementAndNotify() = launchOnDefault {
        setState { it.copy(count = it.count + 1) }
        sendEffect(CounterEffect("incremented to ${currentState.count}"))
    }

    fun failLoad() = launchOnDefault {
        setState { it.copy(loadState = LoadState.Error(UiMessage("error.generic"))) }
    }
}

/** M6.2 -- real, executed proof of `AuraViewModel`'s own contract: state emission through `StateFlow`, one-time effect delivery through the bounded `Channel`, and dispatcher injection (never a hardcoded `Dispatchers.Main`, which does not even exist outside a real Android/JVM-with-Swing main thread). */
@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class AuraViewModelTest {

    @Test
    fun realStateUpdatesAreObservableThroughTheStateFlow() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val vm = CounterViewModel(dispatcher)
        assertEquals(0, vm.state.value.count)

        vm.increment()
        advanceUntilIdle()

        assertEquals(1, vm.state.value.count)
    }

    @Test
    fun multipleRealUpdatesApplyInOrder() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val vm = CounterViewModel(dispatcher)

        vm.increment()
        vm.increment()
        vm.increment()
        advanceUntilIdle()

        assertEquals(3, vm.state.value.count)
    }

    @Test
    fun aRealEffectIsDeliveredExactlyOnceThroughTheChannel() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val vm = CounterViewModel(dispatcher)

        vm.incrementAndNotify()
        advanceUntilIdle()

        val effect = vm.effects
        var received: CounterEffect? = null
        val job = launch { effect.collect { received = it; return@collect } }
        advanceUntilIdle()
        job.cancel()

        assertEquals(CounterEffect("incremented to 1"), received)
    }

    @Test
    fun aLoadStateErrorCarriesARealUiMessageNeverRawText() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val vm = CounterViewModel(dispatcher)

        vm.failLoad()
        advanceUntilIdle()

        val loadState = vm.state.value.loadState
        assertEquals(LoadState.Error(UiMessage("error.generic")), loadState)
        assertNull((loadState as LoadState.Error).message.args.firstOrNull())
    }
}
