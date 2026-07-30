# Phase 8V-P4 — Validation Cleanup

| Item | Action | Confirmed |
|---|---|---|
| `adb reverse tcp:5551 tcp:5551` | Removed (`adb reverse --remove-all`) | `adb reverse --list` empty afterward |
| `adb forward` rules (15000/15001 -> various local ports) | Removed (`adb forward --remove-all`) | `adb forward --list` empty afterward |
| Display size override (`wm size 720x2200`, used to work around the on-screen keyboard covering
  form-submit buttons) | Reset (`wm size reset`) | `wm size` reports physical size `720x1612` only, no override |
| Owner dev server (PID bound to :5551) | Stopped (`Stop-Process -Force`) | `curl` to `:5551` afterward returns connection-refused |
| Temporary screenshots/UI-dump files written to the docs directory during navigation | Deleted | `docs/owner/phase8vp4/` contains only the real evidence `.md` files |
| No firewall rules were added this session (Mode 1 `adb reverse` only) | N/A | Nothing to remove |
| Unredacted traffic containing activation keys | None were ever saved to disk -- all evidence captured this session already excluded the full key outside its one real activation call | N/A |
| Synthetic databases | None packaged -- all synthetic data lives in the real, existing `aura_owner_dev` Postgres database and the two products' own on-device SQLite files, neither of which is part of this repository | N/A |
| Git status | Clean before this doc-set commit (confirmed via `git status --short`) | See `phase8-final-unconditional-decision.md` |

## Result: cleanup complete, confirmed for every item.
