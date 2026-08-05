package com.actionaura.retail.ui.components

import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import com.actionaura.retail.presentation.LoadState
import com.actionaura.retail.presentation.PaginationState
import com.actionaura.retail.presentation.SearchState
import com.actionaura.retail.ui.theme.AuraSpacing

/**
 * M6.15 -- shared list/search/pagination components, built on M6.1's
 * own `PaginationState<T>`/`SearchState` contracts.
 */

@Composable
fun AuraSearchBar(state: SearchState, onQueryChange: (String) -> Unit, placeholder: String, modifier: Modifier = Modifier) {
    OutlinedTextField(
        value = state.rawQuery,
        onValueChange = onQueryChange,
        placeholder = { Text(placeholder) },
        singleLine = true,
        modifier = modifier.fillMaxWidth().padding(horizontal = AuraSpacing.md, vertical = AuraSpacing.xs),
    )
}

/**
 * Real, bounded paginated list -- `onLoadMore` fires only when the
 * real repository-reported `hasMore` is true and the list is not
 * already loading more, never assumed from `items.size` alone
 * (`PaginationState`'s own real contract, M6.1). Callers supply the
 * real item content; this owns only the scroll/load-more mechanics.
 */
@Composable
fun <T> AuraPaginatedList(
    state: PaginationState<T>,
    onLoadMore: () -> Unit,
    modifier: Modifier = Modifier,
    itemContent: @Composable (T) -> Unit,
) {
    LazyColumn(modifier = modifier.fillMaxWidth()) {
        items(state.items) { item -> itemContent(item) }
        if (state.hasMore && !state.isLoadingMore) {
            item {
                androidx.compose.runtime.LaunchedEffect(state.items.size) { onLoadMore() }
            }
        }
        if (state.loadState is LoadState.Loading || state.isLoadingMore) {
            item { AuraLoadingState(modifier = Modifier.padding(AuraSpacing.md)) }
        }
    }
}
