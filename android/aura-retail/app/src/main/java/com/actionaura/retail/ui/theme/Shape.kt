package com.actionaura.retail.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.ui.unit.dp

// The desktop token layer's radius system: control 8 / card 12 / panel 16,
// and deliberately NOT uniform — "a control and a panel having the same
// radius is what makes a UI read as a template" (css/main.css, RADIUS).
// Mapped onto the M3 slots by what each slot actually dresses:
//   extraSmall/small -> controls (menus, chips, text fields)
//   medium           -> cards
//   large/extraLarge -> panels (sheets, dialogs)
val AuraShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(8.dp),
    medium = RoundedCornerShape(12.dp),
    large = RoundedCornerShape(16.dp),
    extraLarge = RoundedCornerShape(16.dp),
)
