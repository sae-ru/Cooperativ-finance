# Реализованный Slice 36: персональная ответственность за роли

Статус: обычное назначение, запрос, независимое решение и отзыв роли включены
в fail-closed `critical-command-assurance-v2`.

Общий production gate «каждая critical command» остаётся открытым:
sanctions/appeals, crisis и node authority ещё должны войти в реестр.

## Контракт

В `CRITICAL_EVENT_TYPES` добавлены пять событий:

- `identity.role_assignment_requested`;
- `identity.role_assignment_activated`;
- `identity.role_assignment_approved`;
- `identity.role_assignment_rejected`;
- `identity.role_assignment_revoked`.

Роль нельзя выдать технической учётной записи без связанного активного
участника. Исполнитель также обязан действовать через постоянное активное
назначение административной роли. Journal повторно проверяет user, member,
assignment и cooperative scope перед подписью.

Обычная кооперативная роль активируется сразу. В assurance записываются
инициатор, кооператив, целевой member, точная роль, idempotency record,
authenticated session и следующий ответственный. После активации им становится
получатель роли.

Привилегированная роль сначала получает `PENDING_APPROVAL`. Запрос передаёт
следующий шаг кооперативу или локальному узлу. Решение принимает другой человек:
инициатор остаётся attester, решающий становится approver. При одобрении
ответственность переходит целевому member; при отказе возвращается кооперативу
или узлу. Отзыв подписывает причину и также возвращает следующий шаг владельцу
scope.

Audit log остаётся вторичным операционным индексом. Доказательством факта и
цепочки ответственности является подписанное событие, которое создаётся в той
же транзакции, что и изменение `RoleAssignment`.

## Проверки

- AST gate связывает все 45 critical event types с реальными `assurance=`
  call sites;
- integration flow проверяет request, independent approval, immediate
  activation, independent rejection и revoke;
- тест читает v2 payload и сравнивает target member, node/cooperative scope и
  `next_responsible`;
- API flow создаёт роль только для user, связанного с active member, и
  подтверждает подписанный request event;
- сценарии включены в `scripts/test-critical-quality.sh`;
- Ruff и strict mypy проходят по `220` production source files;
- полный backend на чистой PostgreSQL-схеме: `255 passed, 1 deselected`;
- independent critical-quality round с migration cycle: `29 passed`;
- отдельный three-node federation acceptance: `1 passed`;
- живой узел: `READY`, journal `434/434` без нарушений;
- отдельная SQL-миграция не требуется: assurance хранится внутри immutable
  signed event payload, исторические события не переписываются.
