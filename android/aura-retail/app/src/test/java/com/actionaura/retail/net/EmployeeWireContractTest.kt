package com.actionaura.retail.net

import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import com.google.gson.JsonParser
import org.junit.Test
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import java.lang.reflect.Method

/**
 * What actually goes over the wire for the employee routes.
 *
 * The screen tests before this pinned SOURCE TEXT. A request class whose field
 * was renamed to something the server does not read (`preset` -> `role_preset`)
 * or a route whose path lost a letter (`/preset` -> `/presets`) still compiled,
 * still passed every wording check, and would have been a 400/404 on the first
 * real tap. So these two halves look at the wire itself: the JSON Gson
 * really produces, and the HTTP verb + path Retrofit really builds from
 * [AuraApi]'s own annotations.
 *
 * Key names are the server's, read from `onboarding_routes.py`:
 * `/name` reads `name`, `/preset` reads `preset`, `/branch-scope` reads
 * `branch_scope_uid`, `/role` reads `role`, `/status` reads `status`,
 * `/pin` reads `pin`; create reads `email`, `role`, `name`, `preset`,
 * `branch_scope_uid`.
 */
class EmployeeWireContractTest {

    private val gson = Gson()

    private fun json(body: Any): Map<String, Any?> {
        val obj = JsonParser.parseString(gson.toJson(body)).asJsonObject
        return obj.entrySet().associate { (k, v) ->
            k to if (v.isJsonNull) null else v.asString
        }
    }

    // ── request bodies: exact keys ───────────────────────────────────────────

    @Test
    fun create_sends_email_role_name_preset_and_branch_scope_uid() {
        assertThat(
            json(CreateEmployeeRequest("a@b.com", "manager", "Sara", "branch_manager", "uid-9"))
        ).containsExactly(
            "email", "a@b.com", "role", "manager", "name", "Sara",
            "preset", "branch_manager", "branch_scope_uid", "uid-9",
        )
    }

    @Test
    fun create_leaves_absent_optionals_out_entirely() {
        // Gson omits nulls: the key must be ABSENT, not present-and-null, or the
        // server's `payload.get('name')` sees a null it has to special-case.
        assertThat(json(CreateEmployeeRequest("a@b.com", "cashier")))
            .containsExactly("email", "a@b.com", "role", "cashier")
    }

    @Test
    fun the_preset_change_key_is_preset() {
        assertThat(json(UpdateEmployeePresetRequest("accountant")))
            .containsExactly("preset", "accountant")
    }

    @Test
    fun the_role_change_key_is_role() {
        assertThat(json(UpdateEmployeeRoleRequest("manager"))).containsExactly("role", "manager")
    }

    @Test
    fun the_name_change_key_is_name_and_a_blank_name_is_sent_as_empty_not_dropped() {
        assertThat(json(UpdateEmployeeNameRequest("Sara"))).containsExactly("name", "Sara")
        // PUT /name clears on blank. If the blank were dropped the server would
        // see an empty body and 400 instead of clearing.
        assertThat(json(UpdateEmployeeNameRequest(""))).containsExactly("name", "")
    }

    @Test
    fun the_branch_scope_key_is_branch_scope_uid() {
        assertThat(json(UpdateEmployeeBranchScopeRequest("uid-9")))
            .containsExactly("branch_scope_uid", "uid-9")
    }

    @Test
    fun the_status_and_pin_keys() {
        assertThat(json(UpdateEmployeeStatusRequest("disabled"))).containsExactly("status", "disabled")
        assertThat(json(SetEmployeePinRequest("1234"))).containsExactly("pin", "1234")
    }

    // ── routes: verb + path + argument shape ─────────────────────────────────

    private data class Route(val verb: String, val path: String)

    private fun routeOf(name: String): Pair<Method, Route> {
        val m = AuraApi::class.java.declaredMethods.single { it.name == name }
        val route = listOfNotNull(
            m.getAnnotation(GET::class.java)?.let { Route("GET", it.value) },
            m.getAnnotation(POST::class.java)?.let { Route("POST", it.value) },
            m.getAnnotation(PUT::class.java)?.let { Route("PUT", it.value) },
            m.getAnnotation(DELETE::class.java)?.let { Route("DELETE", it.value) },
        ).single()
        return m to route
    }

    @Test
    fun every_employee_call_is_the_verb_and_path_the_server_registers() {
        val expected = mapOf(
            "employees" to Route("GET", "api/admin/employees"),
            "createEmployee" to Route("POST", "api/admin/employees"),
            "updateEmployeeRole" to Route("PUT", "api/admin/employees/{id}/role"),
            "updateEmployeePreset" to Route("PUT", "api/admin/employees/{id}/preset"),
            "updateEmployeeBranchScope" to Route("PUT", "api/admin/employees/{id}/branch-scope"),
            "updateEmployeeName" to Route("PUT", "api/admin/employees/{id}/name"),
            "updateEmployeeStatus" to Route("PUT", "api/admin/employees/{id}/status"),
            "setEmployeePin" to Route("PUT", "api/admin/employees/{id}/pin"),
            "clearEmployeePin" to Route("DELETE", "api/admin/employees/{id}/pin"),
        )
        for ((fn, route) in expected) assertThat(routeOf(fn).second).isEqualTo(route)
    }

    @Test
    fun each_write_binds_the_id_to_the_path_and_the_right_request_class_to_the_body() {
        val bodies = mapOf(
            "createEmployee" to CreateEmployeeRequest::class.java,
            "updateEmployeeRole" to UpdateEmployeeRoleRequest::class.java,
            "updateEmployeePreset" to UpdateEmployeePresetRequest::class.java,
            "updateEmployeeBranchScope" to UpdateEmployeeBranchScopeRequest::class.java,
            "updateEmployeeName" to UpdateEmployeeNameRequest::class.java,
            "updateEmployeeStatus" to UpdateEmployeeStatusRequest::class.java,
            "setEmployeePin" to SetEmployeePinRequest::class.java,
        )
        for ((fn, bodyClass) in bodies) {
            val (m, route) = routeOf(fn)
            // Parameter 0 is the first declared parameter; a suspend function's
            // trailing Continuation has no annotations and is ignored.
            val annotated = m.parameterAnnotations.mapIndexedNotNull { i, anns ->
                when {
                    anns.any { it is Path } -> i to "path:" + anns.filterIsInstance<Path>().single().value
                    anns.any { it is Body } -> i to "body"
                    else -> null
                }
            }.toMap()
            if (fn == "createEmployee") {
                assertThat(annotated.values).containsExactly("body")
            } else {
                assertThat(annotated.values).containsExactly("path:id", "body")
                assertThat(route.path).contains("{id}")
            }
            val bodyIndex = annotated.entries.single { it.value == "body" }.key
            assertThat(m.parameterTypes[bodyIndex]).isEqualTo(bodyClass)
        }
        // DELETE carries no body: Retrofit refuses one, and the route needs none.
        val (clear, _) = routeOf("clearEmployeePin")
        assertThat(clear.parameterAnnotations.flatMap { it.toList() }.filterIsInstance<Body>()).isEmpty()
        assertThat(clear.parameterAnnotations.flatMap { it.toList() }.filterIsInstance<Path>().map { it.value })
            .containsExactly("id")
    }
}
