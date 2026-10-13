package com.actionaura.retail.ui.pos

import com.actionaura.retail.net.ModifierGroup

/**
 * Choosing extras for a product, as pure rules (the sheet only draws them). A twin of the
 * desktop's `_openModifierPicker` and of the server's resolver
 * (core/retail/modifiers.py::resolve_line_modifiers), which has the last word and re-validates.
 *
 *  - A group with `min_select >= 1` is REQUIRED; otherwise it is optional.
 *  - `max_select == 1` is "choose one" (radio); anything else is "choose any" / "up to N".
 *  - DEFAULTS pre-select only inside a REQUIRED group: the server substitutes a default for an
 *    EMPTY selection on a required group and never on an optional one, so pre-ticking an optional
 *    default would show (and charge) an extra the cashier never chose.
 */
sealed interface GroupProblem {
    /** [need] more must be picked. */
    data class TooFew(val groupName: String, val min: Int) : GroupProblem
    /** More than [max] picked. */
    data class TooMany(val groupName: String, val max: Int) : GroupProblem
}

fun groupMin(g: ModifierGroup): Int = (g.min_select ?: 0).coerceAtLeast(0)
fun groupMax(g: ModifierGroup): Int? = g.max_select?.takeIf { it >= 0 }
fun isRequired(g: ModifierGroup): Boolean = groupMin(g) >= 1
fun isChooseOne(g: ModifierGroup): Boolean = groupMax(g) == 1

/** The option ids selected when the sheet opens: defaults of REQUIRED groups only (one, for a choose-one group). */
fun defaultSelection(groups: List<ModifierGroup>): Set<String> {
    val out = LinkedHashSet<String>()
    for (g in groups) {
        if (!isRequired(g)) continue
        val defaults = g.options.filter { it.is_default == 1 }
        val cap = groupMax(g) ?: defaults.size
        defaults.take(cap.coerceAtLeast(0)).forEach { out += it.id }
    }
    return out
}

/**
 * Tap on an option. Choose-one: it becomes THE selection of its group (tapping the chosen one of an
 * OPTIONAL group clears it, so the group can return to "none"; a REQUIRED group cannot be emptied).
 * Choose-any: toggles, but a tap that would go past the group's maximum is ignored.
 */
fun toggleOption(groups: List<ModifierGroup>, selected: Set<String>, groupId: String, optionId: String): Set<String> {
    val g = groups.firstOrNull { it.id == groupId } ?: return selected
    if (g.options.none { it.id == optionId }) return selected
    val groupIds = g.options.map { it.id }.toSet()
    val inGroup = selected.filter { it in groupIds }
    return when {
        isChooseOne(g) -> when {
            optionId in selected -> if (isRequired(g)) selected else selected - optionId
            else -> (selected - groupIds) + optionId
        }
        optionId in selected -> selected - optionId
        groupMax(g)?.let { inGroup.size >= it } == true -> selected
        else -> selected + optionId
    }
}

/** Every group whose pick count is outside its bounds, in the groups' order. Empty = good to add. */
fun selectionProblems(groups: List<ModifierGroup>, selected: Set<String>): List<GroupProblem> {
    val out = ArrayList<GroupProblem>()
    for (g in groups) {
        val n = g.options.count { it.id in selected }
        if (n < groupMin(g)) out += GroupProblem.TooFew(g.name, groupMin(g))
        groupMax(g)?.let { if (n > it) out += GroupProblem.TooMany(g.name, it) }
    }
    return out
}

/** The picked options as the wire ids (group order, then option order) and as display picks. */
fun chosenOptions(groups: List<ModifierGroup>, selected: Set<String>): Pair<List<String>, List<ModifierPick>> {
    val ids = ArrayList<String>()
    val picks = ArrayList<ModifierPick>()
    for (g in groups) for (o in g.options) if (o.id in selected) {
        ids += o.id
        picks += ModifierPick(group_name = g.name, name = o.name, price_delta = o.price_delta)
    }
    return ids to picks
}
