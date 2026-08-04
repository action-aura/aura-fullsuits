package com.actionaura.retail.financial

/**
 * Stable machine-readable result type for the financial core -- no
 * exceptions cross the financial/domain/usecases boundary for expected
 * validation failures (matches the governing spec's "stable machine-
 * readable domain errors... no English or Arabic prose inside the core").
 */
sealed class FinancialResult<out T> {
    data class Success<T>(val value: T) : FinancialResult<T>()
    data class Failure(val error: FinancialError) : FinancialResult<Nothing>()

    fun getOrNull(): T? = (this as? Success)?.value

    inline fun <R> map(transform: (T) -> R): FinancialResult<R> = when (this) {
        is Success -> Success(transform(value))
        is Failure -> this
    }
}

inline fun <T> FinancialResult<T>.onSuccess(action: (T) -> Unit): FinancialResult<T> {
    if (this is FinancialResult.Success) action(value)
    return this
}

inline fun <T> FinancialResult<T>.onFailure(action: (FinancialError) -> Unit): FinancialResult<T> {
    if (this is FinancialResult.Failure) action(error)
    return this
}
