package com.actionaura.retail.ui.screens

import android.provider.Settings
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.LocalIndication
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import com.actionaura.retail.net.ApiClient
import com.actionaura.retail.net.LoginRequest
import com.actionaura.retail.ui.brand.AuraAurora
import com.actionaura.retail.ui.brand.AuraMark
import com.actionaura.retail.ui.brand.AuraWordmark
import com.actionaura.retail.ui.i18n.ltrIsolate
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.theme.AccentAction
import com.actionaura.retail.ui.theme.AuraBrand
import com.actionaura.retail.ui.theme.AuraPalette
import com.actionaura.retail.ui.theme.BorderDefault
import com.actionaura.retail.ui.theme.BorderHairline
import com.actionaura.retail.ui.theme.Danger
import com.actionaura.retail.ui.theme.DangerContainer
import com.actionaura.retail.ui.theme.SurfaceApp
import com.actionaura.retail.ui.theme.SurfaceSunken
import com.actionaura.retail.ui.theme.SurfaceTill
import com.actionaura.retail.ui.theme.TextPrimary
import com.actionaura.retail.ui.theme.TextTertiary
import kotlinx.coroutines.launch

/**
 * True when the system has turned off all animations (Settings >
 * Accessibility > Remove animations). Same check as
 * ui/brand/AuraMark.kt's private `systemAnimationsDisabled` -- kept as its
 * own small file-local helper here rather than shared, since this file may
 * only be edited on its own (see the design brief this screen was rebuilt
 * from) and the check is three lines. DESIGN.md §6: reduced motion is
 * respected everywhere, including the sign-in button's press feedback below.
 */
private fun systemAnimationsDisabled(context: android.content.Context): Boolean =
    Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f

@Composable
fun LoginScreen(onLoggedIn: () -> Unit) {
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var passwordVisible by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    fun submit() {
        if (email.isBlank() || password.isBlank()) { error = tr("Enter email and password"); return }
        loading = true; error = null
        scope.launch {
            try {
                val r = ApiClient.get().login(LoginRequest(email.trim(), password))
                if (r.success) {
                    // Wave 1A, Part G: populate admin-gating state at login,
                    // not just on cold-start session restore.
                    com.actionaura.retail.ui.RetailSession.update(r.user)
                    onLoggedIn()
                } else error = r.error ?: tr("Invalid credentials")
            } catch (e: Exception) {
                error = tr("Couldn't reach the server. Try again.")
            } finally { loading = false }
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(SurfaceApp)
            .AuraAurora(),
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                // Second pass (2026-09-08): top padding kept smaller than
                // bottom (28dp vs 48dp) rather than the even 40dp/40dp the
                // first pass used, so with `Arrangement.Center` below the
                // header+card block settles slightly above true geometric
                // centre -- optical centring, the same reason a title is
                // rarely pinned to the exact middle of a poster.
                .padding(start = 28.dp, end = 28.dp, top = 28.dp, bottom = 48.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            // The mark draws itself in once, on this screen only -- see
            // AuraMark's `animate` doc comment; every other caller (AppRoot's
            // loading header) still renders it already complete.
            AuraMark(96.dp, animate = true)
            Spacer(Modifier.height(16.dp))
            AuraWordmark()
            // 8dp -> 10dp (second pass): the mark-to-wordmark gap (16dp) and
            // this one read as the same distance at 8dp, flattening the
            // header into one blob instead of two grouped parts.
            Spacer(Modifier.height(10.dp))
            Text(
                tr("One shop. Every device.").uppercase(),
                style = MaterialTheme.typography.labelMedium,
                // Second pass: TextTertiary is still the right token (per
                // the redesign brief) but at full opacity plus 0.16em
                // tracking it read louder than a tagline should next to the
                // wordmark above it -- a slight alpha trim quiets it without
                // touching the letter-spacing or swapping in a smaller type
                // step, which would have shrunk it out of proportion with
                // that tracking instead.
                color = TextTertiary.copy(alpha = 0.75f),
                letterSpacing = 0.16.em,
            )
            Spacer(Modifier.height(32.dp))

            LoginCard(
                email = email,
                onEmailChange = { email = it },
                password = password,
                onPasswordChange = { password = it },
                passwordVisible = passwordVisible,
                onTogglePasswordVisible = { passwordVisible = !passwordVisible },
                error = error,
                loading = loading,
                onSubmit = ::submit,
            )

            Spacer(Modifier.height(20.dp))
            // BuildConfig.VERSION_NAME is reachable from this module (already
            // read the same way in RetailExtraScreens.kt's About row), so the
            // footer is real build metadata, never a hardcoded string.
            //
            // ltrIsolate, because Arabic reordered it. Rendered bare in an RTL
            // paragraph the bidi algorithm lays "1.0.0-rc.5" out as
            // "rc.5-1.0.0" -- seen on a real Mi Note 10, 2026-09-09. No test
            // caught it: the string this code passes in is correct, and the
            // damage happens inside the text engine.
            Text(
                ltrIsolate(com.actionaura.retail.BuildConfig.VERSION_NAME),
                style = MaterialTheme.typography.labelSmall,
                color = TextTertiary,
            )
        }
    }
}

/**
 * The sign-in card. Not the stock Material3 elevated-card composable this
 * screen used before its 2026-09-08 redesign: a plain [Box] so the
 * background can sit at partial opacity over [AuraAurora]'s wash (the aurora
 * reads faintly through it on dark themes) instead of the flat opaque fill
 * that composable would force.
 */
@Composable
private fun LoginCard(
    email: String,
    onEmailChange: (String) -> Unit,
    password: String,
    onPasswordChange: (String) -> Unit,
    passwordVisible: Boolean,
    onTogglePasswordVisible: () -> Unit,
    error: String?,
    loading: Boolean,
    onSubmit: () -> Unit,
) {
    val cardShape = RoundedCornerShape(20.dp)
    // Second pass (2026-09-08): the 1dp BorderHairline alone read as
    // invisible against the aurora ground -- an outline is not how a real
    // panel catches light. Dark themes get a faint wash (the ground is
    // already near-black, so a little goes far); light themes get a
    // stronger one (a white surface needs more to read as lit rather than
    // just paler).
    val topHighlightAlpha = if (AuraPalette.current.isDark) 0.06f else 0.5f
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .shadow(
                elevation = 18.dp,
                shape = cardShape,
                ambientColor = Color.Black.copy(alpha = 0.25f),
                spotColor = Color.Black.copy(alpha = 0.35f),
            )
            .clip(cardShape)
            .background(SurfaceTill.copy(alpha = 0.92f))
            // The top-edge light catch: white fading to transparent over the
            // first ~40dp, drawn after the fill and before the border so it
            // sits as a wash on the surface rather than tinting the fill
            // itself. `Color.White` is not a palette literal -- it is
            // already used the same way in AuraMark.kt's spark, so
            // ColorTokenContractTest needs no new allowance for it here.
            .drawWithCache {
                val highlightHeightPx = 40.dp.toPx()
                val highlightBrush = Brush.verticalGradient(
                    colors = listOf(Color.White.copy(alpha = topHighlightAlpha), Color.Transparent),
                    startY = 0f,
                    endY = highlightHeightPx,
                )
                onDrawBehind { drawRect(brush = highlightBrush) }
            }
            .border(width = 1.dp, color = BorderHairline, shape = cardShape)
            .padding(24.dp),
    ) {
        Column(Modifier.fillMaxWidth()) {
            FieldLabel(tr("Email"))
            Spacer(Modifier.height(6.dp))
            OutlinedTextField(
                value = email,
                onValueChange = onEmailChange,
                // Second pass: an example beats a repeat of the label right
                // above it ("EMAIL" over "Email" said nothing twice). Routed
                // through tr() like every other UI string here, but with no
                // AR_STRINGS entry -- the example address is Latin script in
                // both languages on purpose, so the fallback-to-English-key
                // behaviour of tr() already gives Arabic the same string.
                placeholder = { Text(tr("name@shop.com")) },
                singleLine = true,
                shape = RoundedCornerShape(12.dp),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email, imeAction = ImeAction.Next),
                colors = fieldColors(),
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(16.dp))

            FieldLabel(tr("Password"))
            Spacer(Modifier.height(6.dp))
            OutlinedTextField(
                value = password,
                onValueChange = onPasswordChange,
                // A literal, not tr(): a run of bullets is not natural-
                // language text, so there is nothing here for Arabic (or
                // any locale) to translate differently.
                placeholder = { Text("••••••••") },
                singleLine = true,
                visualTransformation = if (passwordVisible) VisualTransformation.None else PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { onSubmit() }),
                trailingIcon = {
                    IconButton(onClick = onTogglePasswordVisible) {
                        Icon(
                            imageVector = if (passwordVisible) Icons.Filled.VisibilityOff else Icons.Filled.Visibility,
                            contentDescription = if (passwordVisible) tr("Hide password") else tr("Show password"),
                            tint = TextTertiary,
                        )
                    }
                },
                shape = RoundedCornerShape(12.dp),
                colors = fieldColors(),
                modifier = Modifier.fillMaxWidth(),
            )

            if (error != null) {
                Spacer(Modifier.height(16.dp))
                ErrorBanner(error)
            }

            Spacer(Modifier.height(20.dp))
            SignInButton(loading = loading, onClick = onSubmit)
        }
    }
}

@Composable
private fun FieldLabel(text: String) {
    Text(
        text.uppercase(),
        style = MaterialTheme.typography.labelSmall,
        letterSpacing = 0.08.em,
        color = TextTertiary,
    )
}

/** Shared token-based colours for both fields, so the two never drift apart. */
@Composable
private fun fieldColors() = OutlinedTextFieldDefaults.colors(
    focusedBorderColor = AccentAction,
    unfocusedBorderColor = BorderDefault,
    focusedContainerColor = SurfaceSunken,
    unfocusedContainerColor = SurfaceSunken,
    focusedTextColor = TextPrimary,
    unfocusedTextColor = TextPrimary,
    cursorColor = AccentAction,
)

/**
 * The error state, replacing the old bare red [Text]: an icon plus the
 * message, inside a tinted, bordered panel -- the same "honest states" idiom
 * DESIGN.md §2.5 asks for elsewhere (a refusal says why, it isn't just
 * coloured text). There is no dedicated danger-border token in Color.kt (only
 * `Danger`/`DangerContainer` -- see StockBadge's comment in RetailScreens.kt
 * for the same gap), so the border is [Danger] itself at low alpha, derived
 * rather than a new literal -- ColorTokenContractTest allows `.copy()` on an
 * existing token, just no new `Color(0x...)`.
 */
@Composable
private fun ErrorBanner(message: String) {
    Surface(
        color = DangerContainer,
        shape = RoundedCornerShape(12.dp),
        border = BorderStroke(1.dp, Danger.copy(alpha = 0.35f)),
    ) {
        Row(
            modifier = Modifier.padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                imageVector = Icons.Filled.Warning,
                contentDescription = null,
                tint = Danger,
                modifier = Modifier.size(18.dp),
            )
            Spacer(Modifier.width(8.dp))
            Text(message, color = Danger, style = MaterialTheme.typography.bodyMedium)
        }
    }
}

/**
 * The sign-in action. `RoundedCornerShape(14.dp)`, not a pill -- the owner's
 * exact complaint (2026-09-08) was the old full-width, fully-rounded button
 * reading as a generic template default. A press-scale interaction gives it
 * designed feedback instead of Material's flat ripple alone, skipped
 * entirely when the system has animations off.
 *
 * Second pass (2026-09-08): the SHAPE fixed the pill complaint, but the FILL
 * -- a flat `AccentAction` slab -- was still "Material's default primary
 * container, whatever its corner radius" per the follow-up critique. Fill
 * replaced with the ring's own RingMid -> RingEnd gradient (the same
 * blue-into-teal pairing AuraMark.kt's arc draws), which is why this is a
 * plain [Box] with [Modifier.clickable] rather than a Material [Button]:
 * `ButtonDefaults.buttonColors` has no gradient `containerColor` to give it.
 * `Role.Button` on the clickable modifier keeps the accessibility semantics
 * a real `Button` would have provided for free. Label ink is
 * [AuraBrand.OnBrand], not [OnAccent] or white -- the gradient is light at
 * BOTH ends, so it needs one fixed dark ink verified against both stops; see
 * `OnBrand`'s doc comment in Color.kt for the measured contrast ratios.
 */
@Composable
private fun SignInButton(loading: Boolean, onClick: () -> Unit) {
    val context = LocalContext.current
    val reduceMotion = remember { systemAnimationsDisabled(context) }
    val interactionSource = remember { MutableInteractionSource() }
    val pressed by interactionSource.collectIsPressedAsState()
    val scale by animateFloatAsState(
        targetValue = if (pressed && !reduceMotion) 0.98f else 1f,
        animationSpec = tween(durationMillis = 160),
        label = "signInButtonPressScale",
    )
    val shape = RoundedCornerShape(14.dp)

    Box(
        contentAlignment = Alignment.Center,
        modifier = Modifier
            .fillMaxWidth()
            .height(56.dp)
            .scale(scale)
            .clip(shape)
            .background(
                // linearGradient's default `end = Offset.Infinite` resolves
                // to this box's actual (width, height) at draw time -- the
                // same corner-to-corner span AuraMark's own ring gradient
                // uses, without threading the measured size through
                // drawWithCache by hand.
                brush = Brush.linearGradient(listOf(AuraBrand.RingMid, AuraBrand.RingEnd)),
                shape = shape,
            )
            .clickable(
                interactionSource = interactionSource,
                indication = LocalIndication.current,
                enabled = !loading,
                role = Role.Button,
                onClick = onClick,
            ),
    ) {
        if (loading) {
            CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp, color = AuraBrand.OnBrand)
        } else {
            Text(
                tr("Sign In"),
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.SemiBold,
                letterSpacing = 0.02.em,
                color = AuraBrand.OnBrand,
            )
        }
    }
}
