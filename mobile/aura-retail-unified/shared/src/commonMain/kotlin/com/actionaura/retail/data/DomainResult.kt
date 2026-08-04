package com.actionaura.retail.data

/** M5.1 -- the repository layer's counterpart to `financial.FinancialResult`. */
sealed class DomainResult<out T> {
    data class Success<T>(val value: T) : DomainResult<T>()
    data class Failure(val error: RepositoryError) : DomainResult<Nothing>()

    fun getOrNull(): T? = (this as? Success)?.value

    inline fun <R> map(transform: (T) -> R): DomainResult<R> = when (this) {
        is Success -> Success(transform(value))
        is Failure -> this
    }
}
