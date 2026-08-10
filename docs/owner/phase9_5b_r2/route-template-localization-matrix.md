# Phase 9.5B-R2 — Route/Template Localization Matrix

Entry state (before M3 translation work). "Locale" = English+Arabic template
coverage. "RTL" = logical-property layout coverage (inherited structurally
from `layout/base.html` for all templates regardless of translation status).
"Test" = dedicated render test exists. "Browser" = validated in a real
browser this session.

| Template | Blueprint | Auth required | Locale (entry) | RTL (entry) | Test (entry) | Browser (entry) |
|---|---|---|---|---|---|---|
| layout/base.html | (shared) | n/a | EN+AR | yes | yes | yes |
| auth/*.html (7) | auth | no (pre-auth) | EN+AR | yes | yes | yes |
| employees/*.html (7) | employees | yes | EN+AR | yes | yes | yes |
| profile/*.html (2) | employees (self) | yes | EN+AR | yes | yes | yes |
| dashboard/index.html | dashboard | yes | EN only | structural | no | no |
| audit/list.html, security_events.html, verify_chain.html | audit | yes + permission | EN only | structural | no | no |
| catalog/index.html, plan_new.html, plan_detail.html, versions.html, channels.html | catalog, releases | yes + permission | EN only | structural | no | no |
| customers/list.html, new.html, detail.html | customers | yes + permission | EN only | structural | no | no |
| installations/list.html, new.html, detail.html | installations | yes + permission | EN only | structural | no | no |
| licensing/list.html, new.html, detail.html | licensing | yes + permission | EN only | structural | no | no |
| licensing_admin/*.html (6) | licensing_admin | yes + permission | EN only | structural | no | no |
| staff/list.html, detail.html, invitation_created.html | staff | yes + permission | EN only | structural | no | no |
| subscriptions/list.html, new.html, detail.html | subscriptions | yes + permission | EN only | structural | no | no |
| system/backups.html | system | yes + permission | EN only | structural | no | no |
| commercial_ops/*.html (19) | commercial_ops_ui | yes + permission | EN only | structural | no | no |

All permission requirements were read from each route's `@require_permission`
decorator, not assumed — confirmed unchanged by this wave (no decorator
touched).

This matrix is updated in place as M3/M7/M8 complete; the authoritative
post-wave state is in `owner-wide-localization-completion-report.md` and
`template-render-coverage-report.md`.
