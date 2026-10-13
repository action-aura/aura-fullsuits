package com.actionaura.retail.ui.pos

import com.actionaura.retail.net.ModifierGroup
import com.actionaura.retail.net.ModifierOption
import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import org.junit.Test

class ModifierSelectionTest {

    // The route's JSON, in the shape core/retail/modifiers.py::load_product_modifier_groups returns
    // (ints for flags, null max for "any"): COPIED as the contract the models must read.
    private val serverJson = """
        [{"id":"g-size","name":"Size","min_select":1,"max_select":1,
          "options":[{"id":"o-s","name":"Small","price_delta":0,"cost_delta":0,"is_default":1},
                     {"id":"o-l","name":"Large","price_delta":0.5,"cost_delta":0.2,"is_default":0}]},
         {"id":"g-ex","name":"Extras","min_select":0,"max_select":2,
          "options":[{"id":"o-cheese","name":"Cheese","price_delta":0.75,"cost_delta":0.3,"is_default":1},
                     {"id":"o-bacon","name":"Bacon","price_delta":1,"cost_delta":0.5,"is_default":0},
                     {"id":"o-egg","name":"Egg","price_delta":0.5,"cost_delta":0.2,"is_default":0}]},
         {"id":"g-any","name":"Notes","min_select":0,"max_select":null,"options":[
                     {"id":"o-n1","name":"No onion","price_delta":0,"cost_delta":0,"is_default":0}]}]
    """.trimIndent()

    private val groups: List<ModifierGroup> = Gson().fromJson(serverJson, Array<ModifierGroup>::class.java).toList()

    @Test
    fun the_servers_json_reads_into_the_models() {
        assertThat(groups).hasSize(3)
        assertThat(groups[0].min_select).isEqualTo(1)
        assertThat(groups[2].max_select).isNull()
        assertThat(groups[0].options[0].is_default).isEqualTo(1)
        assertThat(groups[0].options[1].price_delta).isEqualTo(0.5)
    }

    @Test
    fun defaults_preselect_only_inside_required_groups() {
        // Size is required -> Small is ticked; Extras is optional -> Cheese (a default) is NOT
        assertThat(defaultSelection(groups)).containsExactly("o-s")
    }

    @Test
    fun choose_one_replaces_and_a_required_group_cannot_be_emptied() {
        var s = defaultSelection(groups)
        s = toggleOption(groups, s, "g-size", "o-l")
        assertThat(s).containsExactly("o-l")
        s = toggleOption(groups, s, "g-size", "o-l")      // tapping the chosen one of a REQUIRED group
        assertThat(s).containsExactly("o-l")
    }

    @Test
    fun an_optional_choose_one_can_return_to_none() {
        val g = listOf(ModifierGroup("g", "Sauce", 0, 1, listOf(ModifierOption("a", "Mayo"), ModifierOption("b", "Ketchup"))))
        var s = toggleOption(g, emptySet(), "g", "a")
        s = toggleOption(g, s, "g", "b")
        assertThat(s).containsExactly("b")
        s = toggleOption(g, s, "g", "b")
        assertThat(s).isEmpty()
    }

    @Test
    fun choose_any_toggles_and_stops_at_the_maximum() {
        var s = defaultSelection(groups)
        s = toggleOption(groups, s, "g-ex", "o-cheese")
        s = toggleOption(groups, s, "g-ex", "o-bacon")
        val full = toggleOption(groups, s, "g-ex", "o-egg")      // a third of "up to 2": ignored
        assertThat(full).isEqualTo(s)
        assertThat(toggleOption(groups, s, "g-ex", "o-cheese")).doesNotContain("o-cheese")  // and it can still be unticked
    }

    @Test
    fun a_missing_required_pick_and_an_overfull_group_are_both_named() {
        val none = selectionProblems(groups, emptySet())
        assertThat(none).containsExactly(GroupProblem.TooFew("Size", 1))
        val over = selectionProblems(groups, setOf("o-s", "o-cheese", "o-bacon", "o-egg"))
        assertThat(over).containsExactly(GroupProblem.TooMany("Extras", 2))
        assertThat(selectionProblems(groups, setOf("o-s", "o-cheese"))).isEmpty()
    }

    @Test
    fun a_minimum_of_two_is_reported_with_its_number() {
        val g = listOf(ModifierGroup("g", "Toppings", 2, 3, listOf(ModifierOption("a", "A"), ModifierOption("b", "B"), ModifierOption("c", "C"))))
        assertThat(selectionProblems(g, setOf("a"))).containsExactly(GroupProblem.TooFew("Toppings", 2))
        assertThat(selectionProblems(g, setOf("a", "b"))).isEmpty()
    }

    @Test
    fun the_wire_ids_and_the_cart_picks_come_out_in_group_order_with_their_deltas() {
        val (ids, picks) = chosenOptions(groups, setOf("o-bacon", "o-l"))
        assertThat(ids).containsExactly("o-l", "o-bacon").inOrder()
        assertThat(picks).containsExactly(
            ModifierPick("Size", "Large", 0.5), ModifierPick("Extras", "Bacon", 1.0)).inOrder()
    }
}
