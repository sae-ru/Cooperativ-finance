# Architecture Decision Records

ADR фиксирует необратимые или дорогие технические решения. Принятый ADR не
переписывается: изменение создаёт новый ADR со ссылкой `Supersedes`.

| ADR | Решение | Статус |
|---|---|---|
| [0001](ADR-0001-modular-monolith.md) | модульный монолит | Accepted |
| [0002](ADR-0002-postgresql-stateful-core.md) | PostgreSQL как stateful core | Accepted |
| [0003](ADR-0003-state-and-signed-journal.md) | состояние плюс signed journal | Accepted |
| [0004](ADR-0004-transactional-outbox.md) | transactional outbox | Accepted |
| [0005](ADR-0005-event-package-sync.md) | sync пакетами событий | Accepted |
| [0006](ADR-0006-browser-drafts.md) | браузерный offline только drafts | Accepted |
| [0007](ADR-0007-content-addressed-evidence.md) | content-addressed evidence | Accepted |
| [0008](ADR-0008-deterministic-clearing.md) | чистый детерминированный clearing | Accepted |
| [0009](ADR-0009-local-authentication.md) | локальная идентификация обязательна | Accepted |
| [0010](ADR-0010-cryptographic-baseline.md) | cryptographic baseline | Proposed |
| [0011](ADR-0011-mit-independent-implementation.md) | MIT и независимая реализация | Accepted |
| [0012](ADR-0012-role-based-gui.md) | role-based GUI | Accepted |
| [0013](ADR-0013-member-continuity-containment.md) | containment перед economic succession | Accepted/Proposed |

Номер не переиспользуется. Шаблон: Context, Decision, Consequences, Validation.
