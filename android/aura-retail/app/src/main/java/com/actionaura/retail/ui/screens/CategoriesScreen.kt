@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.retail.ui.screens

// Multi-device sync foundation (2026-08-06), Task 9 wiring. Category is the
// only entity type Task 4's sync_outbox wiring understands (see
// commercial_runtime/sync/sync_service.py's own docstring) -- this screen
// is what lets a category be created/edited through this app's REAL UI
// (Products screens only ever read category_name off products; there was
// previously no create/edit surface for categories at all on Android).
// Mirrors SuppliersScreen's exact list+FAB+bottom-sheet shape.

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Category
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.ApiClient
import com.actionaura.retail.net.Category as CategoryModel
import com.actionaura.retail.net.CreateCategoryRequest
import com.actionaura.retail.net.apiErrorMessage
import com.actionaura.retail.sync.SyncCoordinator
import com.actionaura.retail.ui.components.EmptyState
import com.actionaura.retail.ui.components.TillCard
import com.actionaura.retail.ui.components.SkeletonList
import com.actionaura.retail.ui.i18n.tr
import kotlinx.coroutines.launch

@Composable
fun CategoriesScreen(snackbar: SnackbarHostState) {
    var categories by remember { mutableStateOf<List<CategoryModel>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var showAdd by remember { mutableStateOf(false) }
    var editing by remember { mutableStateOf<CategoryModel?>(null) }
    val scope = rememberCoroutineScope()

    suspend fun load() { categories = try { ApiClient.get().categories().data } catch (e: Exception) { emptyList() } }
    LaunchedEffect(Unit) { load(); loading = false }

    Box(Modifier.fillMaxSize()) {
        when {
            loading -> SkeletonList()
            categories.isEmpty() -> EmptyState(Icons.Default.Category, tr("No categories yet"),
                tr("Group products so they're easy to find and filter."), ctaText = tr("Add Category"), onCta = { showAdd = true })
            else -> LazyColumn(
                contentPadding = PaddingValues(16.dp, 16.dp, 16.dp, 90.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                items(categories, key = { it.id }) { c -> CategoryRow(c, onClick = { editing = c }) }
            }
        }
        if (categories.isNotEmpty()) ExtendedFloatingActionButton(
            onClick = { showAdd = true },
            icon = { Icon(Icons.Default.Add, null) }, text = { Text(tr("Add Category")) },
            modifier = Modifier.align(Alignment.BottomEnd).padding(20.dp),
        )
    }

    if (showAdd) AddOrEditCategorySheet(
        existing = null,
        onDismiss = { showAdd = false },
        onSaved = {
            showAdd = false
            scope.launch { loading = true; load(); loading = false; snackbar.showSnackbar(tr("Category added")) }
        },
    )
    editing?.let { c ->
        AddOrEditCategorySheet(
            existing = c,
            onDismiss = { editing = null },
            onSaved = {
                editing = null
                scope.launch { loading = true; load(); loading = false; snackbar.showSnackbar(tr("Category updated")) }
            },
        )
    }
}

@Composable
private fun CategoryRow(c: CategoryModel, onClick: () -> Unit) {
    TillCard(Modifier.fillMaxWidth(), onClick = onClick) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(c.name, fontWeight = FontWeight.Bold)
                if (!c.description.isNullOrBlank()) Text(c.description, style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            AssistChip(onClick = {}, label = { Text("${c.product_count} " + tr("items")) })
        }
    }
}

@Composable
private fun AddOrEditCategorySheet(existing: CategoryModel?, onDismiss: () -> Unit, onSaved: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    var name by remember { mutableStateOf(existing?.name ?: "") }
    var description by remember { mutableStateOf(existing?.description ?: "") }
    var saving by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp).verticalScroll(rememberScrollState())) {
            Text(if (existing == null) tr("Add Category") else tr("Edit Category"),
                style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(16.dp))
            OutlinedTextField(name, { name = it }, label = { Text(tr("Category name *")) },
                singleLine = true, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(description, { description = it }, label = { Text(tr("Description")) },
                modifier = Modifier.fillMaxWidth())
            if (error != null) { Spacer(Modifier.height(10.dp)); Text(error!!, color = MaterialTheme.colorScheme.error) }
            Spacer(Modifier.height(20.dp))
            Button(
                onClick = {
                    if (name.isBlank()) { error = tr("Category name is required"); return@Button }
                    saving = true; error = null
                    scope.launch {
                        try {
                            val body = CreateCategoryRequest(name = name.trim(), description = description.trim())
                            val r = if (existing == null) ApiClient.get().createCategory(body)
                                    else ApiClient.get().updateCategory(existing.id, body)
                            if (r.status == "success") {
                                // Best-effort immediate push -- see
                                // SyncCoordinator.nudge()'s own doc comment.
                                // The regular 10s timer would pick this up
                                // regardless; this just closes the gap for a
                                // snappier real-device demo.
                                SyncCoordinator.nudge()
                                onSaved()
                            } else error = r.message ?: tr("Couldn't save")
                        } catch (e: Exception) { error = apiErrorMessage(e) } finally { saving = false }
                    }
                },
                enabled = !saving, modifier = Modifier.fillMaxWidth().height(52.dp),
            ) {
                if (saving) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.onPrimary)
                else Text(tr("Save Category"), style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}
