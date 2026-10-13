package com.actionaura.retail.net

/**
 * Wire models for the till's three newest reads/fields (ANDROID-3). All three are routes and
 * fields the DESKTOP already uses; nothing here is new on the server.
 *
 *  GET  products/{id}/modifier-groups   -> the SAME function create_sale resolves against, so the
 *                                          sheet and the server can never disagree about what is attached
 *  GET  products/{id}/variants          -> the live, active variants of one parent product
 *  GET  discount/approvers              -> who can be asked for a PIN: {ref, label} and NOTHING else
 *
 * SQLite hands integers for flags (`is_default` 0/1), and Gson refuses an integer where a Boolean is
 * declared, so the flags are Ints here and read through the helpers.
 */
data class ModifierOption(
    val id: String = "", val name: String = "",
    val price_delta: Double = 0.0, val cost_delta: Double = 0.0,
    val is_default: Int = 0,
)

data class ModifierGroup(
    val id: String = "", val name: String = "",
    /** 0 or null = optional. >= 1 = required. */
    val min_select: Int? = 0,
    /** null = no upper bound; 1 = choose exactly one. */
    val max_select: Int? = null,
    val options: List<ModifierOption> = emptyList(),
)

data class ModifierGroupsResponse(val status: String = "", val data: List<ModifierGroup> = emptyList())

data class VariantsResponse(val status: String = "", val data: List<Product> = emptyList())

/** One person the till may ask for a PIN. `ref` is what the sale's `approval.approver` carries. */
data class Approver(val ref: String = "", val label: String = "")

data class ApproversResponse(val status: String = "", val data: List<Approver> = emptyList())

/**
 * A manager's approval of one sale: who, and their PIN. The PIN is held in the approval sheet only
 * while it is typed, sent in the body of the ONE sale it approves, and dropped. The server verifies it;
 * it is never stored, logged or kept in screen state.
 */
data class ApprovalReq(val approver: String, val pin: String) {
    // A data class prints every field; a PIN must never reach a log line by accident.
    override fun toString(): String = "ApprovalReq(approver=$approver, pin=****)"
}
