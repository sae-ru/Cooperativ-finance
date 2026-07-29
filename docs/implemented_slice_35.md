# Реализованный Slice 35: ответственность за полномочия и физическую сохранность

Статус: реализован fail-closed command assurance для account recovery,
break-glass и аварийной передачи физической ответственности за товар.

Общий production gate «каждая critical command» остаётся открытым: в реестр
ещё должны войти обычная выдача и отзыв ролей, sanctions/appeals, crisis,
node trust/key lifecycle и paper/offline authority.

## Security-команды

В `CRITICAL_EVENT_TYPES` добавлены семь событий:

- `identity.account_recovery_requested`;
- `identity.account_recovery_executed`;
- `identity.account_recovery_rejected`;
- `identity.break_glass_requested`;
- `identity.break_glass_activated`;
- `identity.break_glass_rejected`;
- `identity.break_glass_revoked`.

Для recovery подписываются инициатор, независимый approver, затронутый
пользователь, evidence и следующий ответственный. После исполнения
ответственность возвращается конкретному member, который обязан сменить
временный пароль. После запроса следующий шаг принадлежит локальному узлу.

Для break-glass подписываются целевой человек, роль, scope кооператива или
узла, длительность, исходное evidence и независимый approver. После активации
`next_responsible` указывает на назначенного member. После отзыва следующий
шаг снова принадлежит кооперативу или узлу, который должен восстановить
штатный контур полномочий.

Технический user без связанного member не может стать стороной такого
персонального assurance.

## Emergency custody

В реестр добавлены девять событий:

- `responsibility.custody_continuity_started`;
- `responsibility.custody_hold_applied`;
- `responsibility.custody_continuity_blocked`;
- `responsibility.temporary_custodian_approved`;
- `responsibility.custody_continuity_rejected`;
- `responsibility.emergency_custody_accepted`;
- `responsibility.emergency_custody_transferred`;
- `responsibility.temporary_custodian_declined`;
- `responsibility.custody_hold_released`.

Запрос сохраняет maximum loss исходного назначения, evidence неспособности,
склад и перечень партий. Удержание каждой партии имеет точное количество и
единицу. Независимое одобрение передаёт следующий шаг конкретному кандидату с
его постоянным role assignment.

Товар не меняет custodian до личного acceptance кандидата. Acceptance и
каждая передача партии подписывают нового member, роль, количество, единицу,
исходный запрос, approval и physical evidence. После передачи
`next_responsible` остаётся новым custodian.

Отказ, decline, блокировка и снятие hold не являются безымянными служебными
действиями: они подписывают причину и возвращают следующий шаг кооперативу.

## Уточнение exposure

Проверка `ExposureClaim` теперь разрешает `maximum_loss` с unit без
искусственного `amount`. Единица обязательна, если задано `amount` или
`maximum_loss`, и запрещена, если числовой exposure отсутствует.

## Проверки

- AST registry связывает все 40 critical event types с реальными
  `assurance=` call sites;
- identity security E2E проверяет recovery, activation, запрет делегирования
  через break-glass и revoke;
- custody E2E проверяет старого custodian до acceptance, hold, независимое
  approval, личное принятие и передачу партии;
- тесты читают подписанный v2 payload и сравнивают category, maximum loss,
  target member, role assignment и `next_responsible`;
- Ruff и strict mypy проходят без замечаний;
- полный backend на чистой PostgreSQL-схеме:
  `255 passed, 1 deselected`;
- independent critical-quality round с upgrade/downgrade/upgrade:
  `27 passed`.

Отдельная SQL-миграция не требуется: assurance хранится внутри нового
append-only подписанного event payload; исторические события не
переписываются.
