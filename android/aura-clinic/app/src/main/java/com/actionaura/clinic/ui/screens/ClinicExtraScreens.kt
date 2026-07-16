@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.clinic.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.actionaura.clinic.net.*
import com.actionaura.clinic.ui.components.Avatar
import com.actionaura.clinic.ui.components.EmptyState
import com.actionaura.clinic.ui.components.SkeletonList
import kotlinx.coroutines.launch

@Composable
fun DoctorsScreen() {
    var docs by remember { mutableStateOf<List<Doctor>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    LaunchedEffect(Unit) {
        loading = true; docs = try { ApiClient.get().doctors().data } catch (e: Exception) { emptyList() }; loading = false
    }
    ListScaffold(loading, docs.isEmpty(), Icons.Default.MedicalServices,
        "No doctors yet", "Doctors you add will appear here and can be assigned to appointments.") {
        items(docs, key = { it.id }) { d ->
            ElevatedCard(Modifier.fillMaxWidth()) {
                Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                    Avatar(d.name)
                    Spacer(Modifier.width(14.dp))
                    Column(Modifier.weight(1f)) {
                        Text(d.name ?: "—", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                        Text(d.specialty ?: "—", style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
    }
}

@Composable
fun PrescriptionsScreen() {
    var rx by remember { mutableStateOf<List<Prescription>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    LaunchedEffect(Unit) {
        loading = true; rx = try { ApiClient.get().prescriptions().data } catch (e: Exception) { emptyList() }; loading = false
    }
    ListScaffold(loading, rx.isEmpty(), Icons.Default.Medication,
        "No prescriptions yet", "Prescriptions you write during visits will be listed here.") {
        items(rx, key = { it.id }) { r ->
            ElevatedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    Text("Rx #${r.id}  ·  Patient #${r.patient_id ?: "—"}", fontWeight = FontWeight.SemiBold)
                    Text(r.notes ?: (r.items_json ?: "—"), style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

@Composable
fun LabExpensesScreen(snackbar: SnackbarHostState) {
    var items by remember { mutableStateOf<List<LabExpense>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var showAdd by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    suspend fun load() { items = try { ApiClient.get().labExpenses().data } catch (e: Exception) { emptyList() } }
    LaunchedEffect(Unit) { loading = true; load(); loading = false }

    Box(Modifier.fillMaxSize()) {
        ListScaffold(loading, items.isEmpty(), Icons.Default.Science,
            "No lab expenses yet", "Track external lab costs by recording them here.",
            ctaText = "Record Expense", onCta = { showAdd = true }) {
            items(items, key = { it.id }) { e ->
                ElevatedCard(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp)) {
                        Row {
                            Text(e.lab_name ?: "—", fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                            Text("$%.2f".format(e.amount), fontWeight = FontWeight.Bold,
                                color = MaterialTheme.colorScheme.primary)
                        }
                        Text("${e.test_name ?: ""}  ·  ${e.expense_date ?: ""}",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
        ExtendedFloatingActionButton(
            onClick = { showAdd = true }, icon = { Icon(Icons.Default.Add, null) }, text = { Text("Record") },
            modifier = Modifier.align(Alignment.BottomEnd).padding(20.dp),
        )
    }

    if (showAdd) {
        val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
        var lab by remember { mutableStateOf("") }
        var test by remember { mutableStateOf("") }
        var amount by remember { mutableStateOf("") }
        var saving by remember { mutableStateOf(false) }
        ModalBottomSheet(onDismissRequest = { showAdd = false }, sheetState = sheet) {
            Column(Modifier.padding(20.dp).padding(bottom = 24.dp)) {
                Text("Record Lab Expense", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(16.dp))
                OutlinedTextField(lab, { lab = it }, label = { Text("Lab name *") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth())
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(test, { test = it }, label = { Text("Test") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth())
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(amount, { amount = it }, label = { Text("Amount") }, singleLine = true,
                    keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                        keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal),
                    modifier = Modifier.fillMaxWidth())
                Spacer(Modifier.height(20.dp))
                Button(onClick = {
                    if (lab.isBlank()) return@Button
                    saving = true
                    scope.launch {
                        try {
                            ApiClient.get().createLabExpense(CreateLabExpenseRequest(
                                lab_name = lab.trim(), test_name = test.trim(),
                                amount = amount.toDoubleOrNull() ?: 0.0))
                            showAdd = false; snackbar.showSnackbar("Lab expense recorded"); load()
                        } catch (e: Exception) { snackbar.showSnackbar("Couldn't save") } finally { saving = false }
                    }
                }, enabled = !saving, modifier = Modifier.fillMaxWidth().height(52.dp)) {
                    if (saving) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp,
                        color = MaterialTheme.colorScheme.onPrimary)
                    else Text("Save", style = MaterialTheme.typography.labelLarge)
                }
            }
        }
    }
}

/** Shared list scaffold: skeleton list / premium empty state / content list. */
@Composable
private fun ListScaffold(
    loading: Boolean, empty: Boolean,
    icon: ImageVector, emptyTitle: String, emptySubtitle: String,
    ctaText: String? = null, onCta: (() -> Unit)? = null,
    content: androidx.compose.foundation.lazy.LazyListScope.() -> Unit,
) {
    when {
        loading -> SkeletonList(count = 6, modifier = Modifier.fillMaxSize())
        empty -> EmptyState(icon, emptyTitle, emptySubtitle, ctaText, onCta)
        else -> LazyColumn(contentPadding = PaddingValues(16.dp, 16.dp, 16.dp, 96.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp), content = content)
    }
}
