package com.actionaura.retail.ui.activation

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import com.actionaura.retail.licensing.transport.ActivationState
import com.actionaura.retail.licensing.transport.PresentationError
import com.actionaura.retail.ui.components.AuraScaffold
import com.actionaura.retail.ui.localization.AppLocale
import com.actionaura.retail.ui.localization.AuraStrings
import com.actionaura.retail.ui.localization.LocalAppLocale
import com.actionaura.retail.ui.theme.AuraSpacing

/**
 * M9.19 -- the real, shared Compose activation flow, built on real M6
 * design components (`AuraScaffold`) and the real M6.11 localization
 * catalog (`activation-compose-flow.md`). No fake success is ever
 * rendered in production -- every branch below reads directly from
 * [ActivationViewModel]'s own real state.
 */
private fun t(key: String, locale: AppLocale, vararg args: String) = AuraStrings.resolve(key, locale, args.toList())

@Composable
fun ActivationScreen(viewModel: ActivationViewModel) {
    val state by viewModel.state.collectAsState()
    val locale = LocalAppLocale.current

    when (state.activationState) {
        ActivationState.NOT_STARTED -> ActivationWelcomeScreen(locale, onStart = viewModel::onStart)
        ActivationState.CUSTOMER_SESSION_REQUIRED, ActivationState.CUSTOMER_AUTHENTICATING ->
            CustomerSignInScreen(locale, state, onEmailChange = viewModel::onEmailChange, onSignIn = viewModel::onSignIn)
        ActivationState.CUSTOMER_VERIFICATION_REQUIRED ->
            AccountVerificationRequiredScreen(locale, onConfirmed = viewModel::onVerificationConfirmed)
        ActivationState.LICENSE_INPUT_REQUIRED, ActivationState.LICENSE_REJECTED ->
            LicenseEntryScreen(locale, state, onSerialChange = viewModel::onLicenseSerialChange, onSubmit = viewModel::onSubmitLicense, onRetry = viewModel::onRetryLicenseClaim)
        ActivationState.LICENSE_CLAIMING -> LoadingScreen(t("activation.license.title", locale))
        ActivationState.DEVICE_POLICY_LOADING -> LoadingScreen(t("activation.policy.title", locale))
        ActivationState.DEVICE_POLICY_REJECTED -> ErrorScreen(locale, state.presentationError, onRetry = viewModel::onRetryLicenseClaim, onSupport = viewModel::onOpenSupport)
        ActivationState.INSTALLATION_IDENTITY_REQUIRED ->
            DevicePolicyAndLabelScreen(locale, state, onLabelChange = viewModel::onDeviceLabelChange, onConfirm = viewModel::onConfirmDevice)
        ActivationState.READY_TO_ACTIVATE -> ActivationConfirmationScreen(locale, state, onActivate = viewModel::onActivate)
        ActivationState.ACTIVATION_REQUESTING -> LoadingScreen(t("activation.progress.title", locale))
        ActivationState.ACTIVATION_RETRY_AVAILABLE, ActivationState.ACTIVATION_REJECTED ->
            ErrorScreen(locale, state.presentationError, onRetry = viewModel::onRetryActivation, onSupport = viewModel::onOpenSupport)
        ActivationState.DEVICE_LIMIT_REACHED -> DeviceLimitReachedScreen(locale, state, onSupport = viewModel::onOpenSupport, onRestart = viewModel::onRestartFlow)
        ActivationState.PLATFORM_NOT_ALLOWED -> PlatformUnavailableScreen(locale, onSupport = viewModel::onOpenSupport)
        ActivationState.IOS_SERVER_NOT_READY -> IosNotReadyScreen(locale, onSupport = viewModel::onOpenSupport)
        ActivationState.INSTALLATION_REVOKED -> ErrorScreen(locale, PresentationError.Installation.Revoked, onRetry = null, onSupport = viewModel::onOpenSupport)
        ActivationState.ACTIVATION_RESPONSE_RECEIVED -> LoadingScreen(t("activation.progress.title", locale))
        ActivationState.SECURE_PERSISTENCE_REQUIRED -> SecureStorageUnavailableScreen(locale, onSupport = viewModel::onOpenSupport, onRestart = viewModel::onRestartFlow)
        ActivationState.ACTIVATION_COMPLETE -> ActivationResultScreen(locale)
        ActivationState.NETWORK_UNAVAILABLE -> NetworkUnavailableScreen(locale, onRetry = viewModel::onRestartFlow)
        ActivationState.SERVER_UNAVAILABLE -> ErrorScreen(locale, PresentationError.Transport.ServerUnavailable, onRetry = viewModel::onRestartFlow, onSupport = viewModel::onOpenSupport)
        ActivationState.TRANSPORT_NOT_CONFIGURED -> ServiceNotConfiguredScreen(locale)
        ActivationState.CANCELLED -> ActivationWelcomeScreen(locale, onStart = viewModel::onRestartFlow)
        ActivationState.FATAL_CONTRACT_ERROR -> ErrorScreen(locale, PresentationError.Contract.MalformedResponse("fatal"), onRetry = viewModel::onRestartFlow, onSupport = viewModel::onOpenSupport)
    }
}

@Composable
private fun ActivationWelcomeScreen(locale: AppLocale, onStart: () -> Unit) {
    AuraScaffold(title = t("activation.welcome.title", locale)) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(AuraSpacing.lg)) {
            Text(t("activation.welcome.body", locale))
            Button(onClick = onStart, modifier = Modifier.padding(top = AuraSpacing.md)) { Text(t("activation.welcome.start", locale)) }
        }
    }
}

@Composable
private fun CustomerSignInScreen(locale: AppLocale, state: ActivationUiState, onEmailChange: (String) -> Unit, onSignIn: (String) -> Unit) {
    var password by remember { mutableStateOf("") }
    AuraScaffold(title = t("activation.signin.title", locale)) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(AuraSpacing.lg)) {
            OutlinedTextField(
                value = state.emailInput, onValueChange = onEmailChange, label = { Text(t("activation.signin.email", locale)) },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email), singleLine = true,
                modifier = Modifier.fillMaxWidth().padding(vertical = AuraSpacing.xs),
            )
            OutlinedTextField(
                value = password, onValueChange = { password = it }, label = { Text(t("activation.signin.password", locale)) },
                visualTransformation = PasswordVisualTransformation(), singleLine = true,
                modifier = Modifier.fillMaxWidth().padding(vertical = AuraSpacing.xs),
            )
            state.presentationError?.let { PresentationErrorText(locale, it) }
            Button(
                onClick = { onSignIn(password); password = "" },
                enabled = !state.submitting,
                modifier = Modifier.padding(top = AuraSpacing.md),
            ) { Text(t("activation.signin.submit", locale)) }
        }
    }
}

@Composable
private fun AccountVerificationRequiredScreen(locale: AppLocale, onConfirmed: () -> Unit) {
    AuraScaffold(title = t("activation.verification.title", locale)) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(AuraSpacing.lg)) {
            Text(t("activation.verification.body", locale))
            Button(onClick = onConfirmed, modifier = Modifier.padding(top = AuraSpacing.md)) { Text(t("action.retry", locale)) }
        }
    }
}

@Composable
private fun LicenseEntryScreen(locale: AppLocale, state: ActivationUiState, onSerialChange: (String) -> Unit, onSubmit: () -> Unit, onRetry: () -> Unit) {
    AuraScaffold(title = t("activation.license.title", locale)) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(AuraSpacing.lg)) {
            OutlinedTextField(
                value = state.licenseSerialInput, onValueChange = onSerialChange, label = { Text(t("activation.license.serial", locale)) }, singleLine = true,
                modifier = Modifier.fillMaxWidth().padding(vertical = AuraSpacing.xs),
            )
            state.presentationError?.let { PresentationErrorText(locale, it) }
            Button(onClick = onSubmit, enabled = !state.submitting, modifier = Modifier.padding(top = AuraSpacing.md)) { Text(t("activation.license.submit", locale)) }
            if (state.activationState == ActivationState.LICENSE_REJECTED) {
                TextButton(onClick = onRetry) { Text(t("action.retry", locale)) }
            }
        }
    }
}

@Composable
private fun DevicePolicyAndLabelScreen(locale: AppLocale, state: ActivationUiState, onLabelChange: (String) -> Unit, onConfirm: () -> Unit) {
    AuraScaffold(title = t("activation.policy.title", locale)) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(AuraSpacing.lg)) {
            state.devicePolicy?.let { policy ->
                Text(t("activation.policy.remaining", locale, policy.remainingInstallationSlots.toString(), policy.totalActiveInstallationLimit.toString()))
            }
            OutlinedTextField(
                value = state.deviceLabelInput, onValueChange = onLabelChange, label = { Text(t("activation.device.label", locale)) }, singleLine = true,
                modifier = Modifier.fillMaxWidth().padding(vertical = AuraSpacing.md),
            )
            Button(onClick = onConfirm) { Text(t("action.save", locale)) }
        }
    }
}

@Composable
private fun ActivationConfirmationScreen(locale: AppLocale, state: ActivationUiState, onActivate: () -> Unit) {
    AuraScaffold(title = t("activation.confirm.title", locale)) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(AuraSpacing.lg)) {
            Text(state.deviceLabelInput)
            Button(onClick = onActivate, enabled = !state.submitting, modifier = Modifier.padding(top = AuraSpacing.md)) { Text(t("activation.confirm.submit", locale)) }
        }
    }
}

@Composable
private fun LoadingScreen(title: String) {
    AuraScaffold(title = title) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(AuraSpacing.lg)) {
            CircularProgressIndicator()
        }
    }
}

@Composable
private fun ErrorScreen(locale: AppLocale, error: PresentationError?, onRetry: (() -> Unit)?, onSupport: () -> Unit) {
    AuraScaffold(title = t("error.generic", locale)) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(AuraSpacing.lg)) {
            error?.let { PresentationErrorText(locale, it) }
            onRetry?.let { retry -> Button(onClick = retry, modifier = Modifier.padding(top = AuraSpacing.md)) { Text(t("action.retry", locale)) } }
            TextButton(onClick = onSupport) { Text(t("activation.support.action", locale)) }
        }
    }
}

@Composable
private fun DeviceLimitReachedScreen(locale: AppLocale, state: ActivationUiState, onSupport: () -> Unit, onRestart: () -> Unit) {
    AuraScaffold(title = t("activation.device_limit.title", locale)) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(AuraSpacing.lg)) {
            state.devicePolicy?.let { Text(t("activation.policy.remaining", locale, "0", it.totalActiveInstallationLimit.toString())) }
            TextButton(onClick = onSupport) { Text(t("activation.support.action", locale)) }
            TextButton(onClick = onRestart) { Text(t("action.retry", locale)) }
        }
    }
}

@Composable
private fun PlatformUnavailableScreen(locale: AppLocale, onSupport: () -> Unit) {
    AuraScaffold(title = t("activation.platform_unavailable.title", locale)) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(AuraSpacing.lg)) {
            TextButton(onClick = onSupport) { Text(t("activation.support.action", locale)) }
        }
    }
}

@Composable
private fun IosNotReadyScreen(locale: AppLocale, onSupport: () -> Unit) {
    // Real, honest -- never claims iOS activation is available (ios-platform-readiness-state.md).
    AuraScaffold(title = t("activation.ios_not_ready.title", locale)) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(AuraSpacing.lg)) {
            TextButton(onClick = onSupport) { Text(t("activation.support.action", locale)) }
        }
    }
}

@Composable
private fun SecureStorageUnavailableScreen(locale: AppLocale, onSupport: () -> Unit, onRestart: () -> Unit) {
    AuraScaffold(title = t("activation.storage_unavailable.title", locale)) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(AuraSpacing.lg)) {
            Text(t("activation.storage_unavailable.body", locale))
            TextButton(onClick = onSupport) { Text(t("activation.support.action", locale)) }
            TextButton(onClick = onRestart) { Text(t("action.retry", locale)) }
        }
    }
}

@Composable
private fun ActivationResultScreen(locale: AppLocale) {
    AuraScaffold(title = t("activation.result.complete", locale)) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(AuraSpacing.lg)) {
            Text(t("activation.result.complete", locale), style = MaterialTheme.typography.titleLarge)
        }
    }
}

@Composable
private fun NetworkUnavailableScreen(locale: AppLocale, onRetry: () -> Unit) {
    AuraScaffold(title = t("activation.network_unavailable.title", locale)) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(AuraSpacing.lg)) {
            Button(onClick = onRetry) { Text(t("action.retry", locale)) }
        }
    }
}

@Composable
private fun ServiceNotConfiguredScreen(locale: AppLocale) {
    // The real, honest state every production build shows today -- DisabledProductionTransport never fabricates success.
    AuraScaffold(title = t("activation.not_configured.title", locale)) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(AuraSpacing.lg)) {
            Text(t("activation.not_configured.body", locale))
        }
    }
}

@Composable
private fun PresentationErrorText(locale: AppLocale, error: PresentationError) {
    Text(t("error.generic", locale), color = MaterialTheme.colorScheme.error)
}
