# Реализованный Slice 24: жизненный цикл внешних интеграций

## Назначение

Slice 24 отделяет машинный доступ от человеческих учётных записей. Внешняя программа получает собственный `ServiceClient`, принадлежит ровно одному кооперативу, имеет именованного технического ответственного, минимальный набор разрешений, сетевой allowlist, лимит запросов и конечный срок. Отключение или компрометация интеграции не блокирует вход сотрудников.

`ServiceClient` не является `User`, `Member` или `Node`. Машинный bearer token не принимается человеческими `/api/v1/auth` и `/api/v1/admin` endpoints, а пользовательская сессия не подменяет отдельные credentials интеграции.

## Жизненный цикл и ответственность

Создание, изменение настроек, ротация секрета и возобновление оформляются заявкой:

1. пользователь с постоянной ролью `COOPERATIVE_ADMIN` или `SECURITY_ADMIN` создаёт заявку в scope кооператива;
2. другой персонально связанный пользователь с постоянной ролью `SECURITY_ADMIN` и действующим TOTP step-up проверяет заявку;
3. сервер повторно проверяет owner, version, состояние, срок, scopes, allowlist и конфликт имени;
4. решение создаёт подписанное событие и обычную audit-запись с request id;
5. при создании или ротации новый секрет возвращается только в первом успешном ответе.

Создатель не может одобрить свою заявку. Break-glass grant не считается постоянным правом для управления интеграциями. Все изменяемые команды используют `Idempotency-Key` и `expected_version`. Повтор решения не возвращает секрет ещё раз.

Приостановка и безвозвратный отзыв являются защитными командами `SECURITY_ADMIN`: они требуют step-up, выполняются немедленно и отзывают все активные machine tokens. Возобновление приостановленного клиента снова требует заявки и независимого решения. Отозванный клиент не восстанавливается.

## Credentials и сетевой доступ

Секрет имеет высокую энтропию и связан с отдельным credential id. В БД сохраняются только SHA-256 и безопасный префикс, открытый секрет не попадает в journal, audit, idempotency payload или логи.

Получение machine token проверяет одновременно:

- `client_code` и активный credential;
- активный кооператив-владелец;
- состояние и срок самого клиента;
- точный source IP против CIDR allowlist;
- PostgreSQL-backed лимит запросов в текущую минуту.

`0.0.0.0/0` и `::/0` запрещены. Source IP считается доверенным только за штатным gateway: API не должен публиковаться в обход него, а заголовок forwarded address должен формировать только доверенный reverse proxy.

Machine token короткоживущий, хранится на сервере в отзываемом виде и дополнительно связан с credential и исходным IP. Доступны только два минимальных scope:

- `catalog:read` - bounded поиск товаров и услуг без direct peer fanout;
- `clearing:accounting:read` - чтение готовой бухгалтерской выгрузки клиринга только своего кооператива.

## API

```text
GET  /api/v1/admin/service-clients
GET  /api/v1/admin/service-client-requests
POST /api/v1/admin/service-client-requests
POST /api/v1/admin/service-client-requests/{request_id}/decision
POST /api/v1/admin/service-clients/{client_id}/suspend
POST /api/v1/admin/service-clients/{client_id}/revoke

POST /api/v1/service-auth/token
GET  /api/v1/service/context
POST /api/v1/service/catalog/search
GET  /api/v1/service/clearing/cycles/{cycle_id}/accounting-export
```

Administrative lists are server-side scoped. Global `SECURITY_ADMIN` and `AUDITOR` can read all owners; cooperative roles see only permitted owners. Runtime endpoints call `require_scope` independently for every operation.

## Данные и миграция

Revision `0031_service_client_lifecycle` adds:

- `identity.service_clients` - owner, contact, scopes, allowlist, rate, status, expiry and version;
- `identity.service_client_credentials` - hashed credential lifecycle with at most one active credential;
- `identity.service_client_requests` - versioned dual-control request and independent reviewer;
- `identity.service_client_access_tokens` - short-lived revocable source-bound tokens;
- `identity.service_client_rate_buckets` - database-backed per-minute counters.

Database constraints enforce legal statuses, nonempty JSON arrays, bounds, one active credential, one pending request per existing client and independent reviewer. The worker marks expired active tokens and retains finished tokens for 30 days and rate buckets for two days, preventing unbounded runtime-table growth while preserving a bounded incident window.

## Административный интерфейс

В реестре системы появилась отдельная вкладка **Интеграции**. Она не смешана с учётными записями людей. Оператор видит владельца, ответственного, понятные названия разрешений, сети, лимит, срок и состояние. Форма по умолчанию выдаёт только поиск каталога, а поле сети оставляет обязательным и пустым, поэтому сетевой доступ и его расширение являются явными действиями.

Очередь заявок показывает, кто создал запрос и до какого времени он действителен. Собственная заявка помечается как требующая другого проверяющего. Защищённое решение, приостановка и отзыв открывают отдельное подтверждение с TOTP. После создания или ротации client id и secret показываются в отдельном окне с предупреждением об одноразовой выдаче и отдельными кнопками копирования.

Все системные подписи, состояния, ошибки и подтверждения находятся в `lang/ru.xml` и `lang/en.xml`. Значения, введённые владельцем, помечены как пользовательские данные и не переводятся.

## Демоданные

`seed-demo` создаёт идемпотентно активную интеграцию `svc_demo_catalog_bridge` и заявку регистратора на ротацию её секрета. Открытый начальный секрет не хранится. Администратор `security` может пройти независимое решение в интерфейсе и получить новый секрет один раз, что даёт воспроизводимый учебный сценарий без обхода production-инвариантов.

## Проверки

Автоматические проверки покрывают нормализацию scopes/CIDR/expiry/rate, dual control, одноразовую выдачу секрета, раздельность machine/human token, source binding, отзыв без остановки человеческой сессии, signed events, runtime cleanup, идемпотентный demo seed, API-клиент, RU/EN GUI, self-review guard и TOTP-подтверждение.

Финальный checkpoint подтверждён: backend — `207 passed, 1 deselected` (внешний acceptance); frontend — `61` test file и `170` tests, coverage `81.91%` statements / `75.08%` functions / `88.10%` lines, typecheck и production PWA build; Ruff — без замечаний, mypy — `205` source files; RU/EN XML — по `774` уникальных и совпадающих ключа. Миграция прошла цикл `0031 -> 0030 -> 0031` и `alembic check`. Живой Docker-узел имеет статус `OPERATIONAL`, schema revision `0031_service_client_lifecycle`, работающий worker, изолированные сети и единственный опубликованный gateway `8080`. В браузере проверены RU/EN, light/dark, демо-клиент и pending rotation request; console errors отсутствуют.

## Оставшиеся production gates

Кодовый срез не заменяет независимый security review доверенной proxy-границы, выдачу реальных сетевых диапазонов, секрет-хранилище внешней программы, incident drill с компрометацией credential, целевую нагрузку и утверждение владельцев интеграций. Управляемое объединение подтверждённых дубликатов остаётся следующим отдельным срезом.