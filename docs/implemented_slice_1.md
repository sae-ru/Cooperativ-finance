# Реализованный Slice 1: identity, доступ и аудит

Статус: реализовано и проверено на Linux-контейнерах с PostgreSQL. Этот документ
фиксирует фактическое состояние кода, а не заменяет требования следующих срезов.

## Что работает

- отдельные сущности `UserAccount`, `Member`, `Cooperative`, `Membership` и
  `RoleAssignment`;
- жизненный цикл участника с проверяемыми переходами и optimistic locking;
- локальный вход с Argon2id, блокировкой перебора и одинаковым ответом для
  неизвестного логина и неверного пароля;
- непрозрачные access/refresh-токены: исходные значения не сохраняются в БД;
- короткий access-токен, серверная refresh-сессия, ротация, отзыв и немедленная
  проверка активных ролей по БД;
- HttpOnly refresh-cookie, SameSite Strict и отдельная CSRF-cookie;
- обязательная смена bootstrap-пароля;
- scoped RBAC без роли суперпользователя;
- запрет самоназначения роли;
- привилегированные роли `SECURITY_ADMIN`, `NODE_REGISTRAR` и `AUDITOR`
  создаются в `PENDING_APPROVAL` и требуют решения другого оператора;
- append-only журнал `journal.audit_entries`;
- реестр идемпотентности для административных команд;
- административные API и PWA-интерфейс для участников, членств, учетных записей,
  ролей, сессий и аудита;
- детерминированные демоданные и интеграционные тесты на реальном PostgreSQL.

## Начальная установка

`scripts/bootstrap-node.sh` и `scripts/bootstrap-node.ps1` создают шесть файловых
секретов: два пароля БД, seed узла и временные пароли трех операторов. Compose
выполняет цепочку:

```text
postgres -> migrate -> init-node -> bootstrap-identity -> api/worker
                                      |
                                      +-> seed-demo (только профиль demo)
```

Bootstrap создает учетные записи только при пустой таблице пользователей:

| Логин | Начальные полномочия | Файл временного пароля |
|---|---|---|
| `registrar` | `MEMBER_REGISTRAR`, `COOPERATIVE_ADMIN` локального кооператива | `secrets/bootstrap_registrar_password` |
| `security` | `SECURITY_ADMIN` | `secrets/bootstrap_security_password` |
| `auditor` | `AUDITOR` | `secrets/bootstrap_auditor_password` |

Повторный запуск не меняет существующие пароли и роли. Каждый оператор обязан
сменить временный пароль при первом входе. После смены всех трех паролей исходные
bootstrap-секреты следует удалить из эксплуатационного хранилища по утвержденной
процедуре и сохранить только резервный доступ, предусмотренный политикой узла.

## Модель безопасности

- в БД сохраняются только Argon2id-хэши паролей и SHA-256 fingerprints токенов;
- смена пароля отзывает все прежние сессии;
- refresh одновременно заменяет access, refresh и CSRF-токены;
- старый access-токен перестает работать сразу после refresh;
- отзыв сессии и роли учитывается на следующем API-запросе;
- идентификатор участника используется для проверки дублей только в виде хэша;
- audit не содержит паролей, токенов и исходных идентификаторов;
- runtime-роль БД имеет только `SELECT/INSERT` к audit, а триггер запрещает
  `UPDATE/DELETE` независимо от прикладного кода;
- административный отказ для аутентифицированного оператора также записывается
  в audit без тела запроса.

## Основные API

```text
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
GET  /api/v1/auth/me
POST /api/v1/auth/change-password

GET  /api/v1/admin/overview
GET|POST /api/v1/admin/cooperatives
GET|POST /api/v1/admin/members
POST /api/v1/admin/members/{id}/transitions
GET|POST /api/v1/admin/memberships
GET|POST /api/v1/admin/users
GET|POST /api/v1/admin/roles
POST /api/v1/admin/roles/{id}/decision
POST /api/v1/admin/roles/{id}/revoke
GET  /api/v1/admin/sessions
POST /api/v1/admin/sessions/{id}/revoke
GET  /api/v1/admin/audit
```

Создающие и изменяющие административные команды требуют `Idempotency-Key`.
Полный машинный контракт находится в `backend/openapi.json` и
`frontend/openapi.json`.

## Проверка

Backend проверяется Ruff, strict mypy и Pytest на реальном PostgreSQL. Набор
включает unit-, API- и integration-тесты аутентификации, lockout, refresh-ротации,
отзыва, идемпотентности, bootstrap и двойного контроля ролей.

Frontend проверяется TypeScript, Vitest с coverage и production PWA build.
Проверяются вход, обязательная смена пароля, role-aware навигация, участники,
членства, учетные записи, роли, сессии, audit и деградированное состояние узла.

Экран обязательной смены bootstrap-пароля содержит штатный выход из сессии. На
мобильном экране активный пункт длинной role-aware навигации автоматически
перемещается в видимую область, при этом горизонтальная прокрутка остается доступной.
## Что еще не готово

Реализация Slice 1 не означает production readiness всей экономической системы.
На момент завершения Slice 1 отсутствовали подписанная цепочка хозяйственных событий и персональная ответственность. Они реализованы в [Slice 2](implemented_slice_2.md). Паи и ограничение риска, товарные права, сделки, двусторонний и межузловой клиринг, федеративный поиск, кризисный режим, полный backup/restore и эксплуатационная аттестация остаются будущими срезами.
