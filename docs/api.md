# API

Статус: обязательные conventions для OpenAPI `/api/v1`.

## Общие правила

- JSON UTF-8; binary upload выполняется отдельным endpoint;
- snake_case в JSON;
- UUID передаётся строкой;
- UTC timestamp в RFC3339;
- Decimal передаётся строкой, не JSON number;
- количество всегда содержит `value`, `unit_code`, `scale`;
- create/transition endpoints требуют `Idempotency-Key`;
- критические команды возвращают `event_id`;
- клиент не передаёт вычисляемые роли, лимиты или итоговую экспозицию как
  доверенные значения;
- OpenAPI генерируется и проверяется на breaking changes.

## Аутентификация

- короткоживущий access token;
- server-side revocable refresh session;
- CSRF-защита при cookie transport;
- TOTP/WebAuthn step-up для критических ролей;
- service client использует отдельные credentials, scopes и network policy;
- внешний OIDC не является обязательным.

Реализованный локальный контур:

```text
GET    /api/v1/auth/security
POST   /api/v1/auth/totp/enrollment
POST   /api/v1/auth/totp/enrollment/confirm
POST   /api/v1/auth/step-up/totp
DELETE /api/v1/auth/totp

GET/POST /api/v1/admin/account-recoveries
POST     /api/v1/admin/account-recoveries/{id}/decision
GET/POST /api/v1/admin/break-glass
POST     /api/v1/admin/break-glass/{id}/decision
POST     /api/v1/admin/break-glass/{id}/revoke
```

TOTP enrollment возвращает seed и provisioning URI только в ответе создания.
Step-up grant находится в server-side session и не доверяется полю access token.
Recovery и break-glass команды требуют `Idempotency-Key`, reason/evidence,
активный step-up и независимого второго человека. Command `event_id` ссылается
на подписанный node event, а не только на обычную audit-запись. WebAuthn пока не
входит в реализованный OpenAPI и остаётся отдельным production gate.

## Реализованный API внешних программ

Human и machine authentication разделены. `ServiceClient` сначала проходит
административную заявку и независимое решение:

```text
GET  /api/v1/admin/service-clients
GET  /api/v1/admin/service-client-requests
POST /api/v1/admin/service-client-requests
POST /api/v1/admin/service-client-requests/{request_id}/decision
POST /api/v1/admin/service-clients/{client_id}/suspend
POST /api/v1/admin/service-clients/{client_id}/revoke
```

Create/update/rotate/reactivate требуют permanent manager role,
`Idempotency-Key` и versioned state. Decision, suspend и revoke дополнительно
требуют permanent `SECURITY_ADMIN`, персонального member, active step-up и не
допускают self-review. Secret возвращается только при первом успешном create или
rotate decision и никогда не включается в replay response.

```text
POST /api/v1/service-auth/token
GET  /api/v1/service/context
POST /api/v1/service/catalog/search
GET  /api/v1/service/clearing/cycles/{cycle_id}/accounting-export
```

Token endpoint принимает `client_id` и `client_secret`, проверяет credential,
client/cooperative expiry/status, trusted source IP и PostgreSQL rate bucket.
Machine bearer token не принимается в human endpoints. `catalog:read` разрешает
только bounded local/indexed/offline search; `DIRECT` fanout запрещён.
`clearing:accounting:read` возвращает только готовый export своего owner
cooperative. Подробности и эксплуатационные границы:
[implemented_slice_24.md](implemented_slice_24.md).

## Формат успешной команды

```json
{
  "data": {
    "id": "uuid",
    "status": "VERIFIED",
    "version": 3
  },
  "event_id": "uuid",
  "request_id": "uuid"
}
```

## Формат ошибки

```json
{
  "error": {
    "code": "INVENTORY_INSUFFICIENT_AVAILABLE_QUANTITY",
    "message_key": "errors.inventory.insufficient_available_quantity",
    "parameters": {
      "available": "12.000",
      "requested": "15.000",
      "unit_code": "kg"
    },
    "field_errors": [],
    "retryable": false
  },
  "request_id": "uuid"
}
```

Сообщение не раскрывает ключи, SQL, stack trace, персональные данные или
внутреннюю топологию.

## HTTP semantics

| Ситуация | Код |
|---|---|
| успешное чтение/изменение | 200 |
| создание | 201 |
| асинхронная некритичная задача | 202 |
| неверный ввод | 400/422 |
| нет сессии | 401 |
| недостаточно полномочий | 403 |
| объект не найден или скрыт политикой | 404 |
| конфликт состояния/version | 409 |
| нарушен лимит | 422 с domain code |
| rate limit | 429 |
| временная локальная деградация | 503 |

## Конкурентные изменения

Изменяемый ресурс возвращает `version` и ETag. Команда перехода передаёт
`expected_version`. Несовпадение возвращает `AGGREGATE_VERSION_CONFLICT` и
актуальную безопасную сводку.

## Списки и фильтры

- cursor pagination для событий и больших журналов;
- bounded `limit`, по умолчанию 50;
- stable sort с UUID tie-breaker;
- allowlist фильтров и сортировок;
- export является отдельной аудируемой операцией.

## Двухэтапное подтверждение

Критическая операция имеет ресурс approval:

```text
POST /operations/{type}/prepare
POST /approvals/{id}/attest
POST /approvals/{id}/finalize
POST /approvals/{id}/cancel
```

Prepare возвращает неизменяемый summary hash, экспозицию, требуемые роли,
истечение и human-readable preview. Attester не может быть тем же человеком,
если политика требует независимость.

## Вложения

1. Создать upload intent с размером, типом и ожидаемым SHA-256.
2. Передать stream с лимитом размера.
3. Сервер проверяет hash и сохраняет encrypted blob.
4. Команда ссылается на immutable evidence id.

Изменение файла создаёт новый blob id. Download проверяет права и журналируется.

## API modules

Полный перечень endpoints задан разделом 14 ТЗ. Реализация выполняется
вертикальными slices; endpoint не публикуется до domain, authorization,
idempotency, audit, OpenAPI и integration tests.

## Профильные API-контракты

- административный lifecycle: [admin_console.md](admin_console.md);
- операционный клиринг: [clearing_operations.md](clearing_operations.md);
- onboarding и lifecycle узла: [node_onboarding.md](node_onboarding.md);
- ответственность и exposure узла: [node_liability_policy.md](node_liability_policy.md);
- федеративный поиск и логистика: [federated_catalog_search.md](federated_catalog_search.md);
- межузловой клиринг: [inter_node_clearing.md](inter_node_clearing.md).

OpenAPI обязан реализовывать перечисленные там команды и scopes; общие CRUD
endpoints не заменяют state transitions, approvals и audit.

## Реализованный exchange API

Slice 5 публикует scoped чтение сделок, обязательств, исполнений, актов
приёмки, логистики и споров в `/api/v1/exchange`. Команды покрывают предложение
и новую версию сделки, личные подтверждения сторон, предъявление и приёмку
исполнения, открытие и независимое решение спора, explicit overdue scan и
переходы заказа доставки.

Все exchange-команды требуют `Idempotency-Key`. Условия подтверждаются точной
парой `terms_version`/`terms_hash`; изменяемые deal, obligation, fulfillment,
logistics order и dispute требуют `expected_version`. Read endpoints применяют
participant/carrier/cooperative scope и скрывают чужой объект как `404`.
Подробный перечень: [implemented_slice_5.md](implemented_slice_5.md).

## Реализованный bounded risk API

Slice 6 публикует participant- и cooperative-scoped реестры политик, паевых
счетов, append-only взносов, связанных лиц, commitments и liability cases в
`/api/v1/risk`.

Команды покрывают dual-control политику, открытие счёта и взнос, preview всех
лимитов, предложение и личное принятие точного `terms_hash`, освобождение
резерва, независимое решение связанности, открытие и оценку ответственности.
Каждая изменяющая команда требует `Idempotency-Key`; переход существующего
агрегата требует `expected_version`.

Preview не создаёт хозяйственного события. Liability assessment фиксирует
`NOT_EXECUTED` и не меняет баланс пая. Подробный перечень endpoints, roles и
инвариантов: [implemented_slice_6.md](implemented_slice_6.md).

## Реализованный local clearing API

Slice 7 публикует scoped lifecycle в `/api/v1/clearing`: policy, cycles,
frozen input, entries, positions, approvals, evidence-backed disputes, proof,
participant statements и accounting export draft.

Каждая изменяющая команда требует `Idempotency-Key`, а переход существующего
цикла, policy или dispute требует `expected_version`. Preview approval связан с
точными `input_hash` и `result_hash`; finalize повторно принимает `result_hash`
и под PostgreSQL locks проверяет версии frozen obligations. Участник читает
только собственный контур, клиринговые роли действуют в cooperative scope,
глобальный аудитор имеет read-only доступ.

`POST /api/v1/clearing/proofs/verify` пересчитывает canonical hashes и чистый
engine без доверия к сохранённым итогам. Полный lifecycle и перечень endpoints:
[implemented_slice_7.md](implemented_slice_7.md).
## Реализованный disputes and trust API

Slice 8 публикует 27 scoped paths в `/api/v1/trust`: dual-control policy,
дела и ответы, conflict declarations, reasoned decisions, protective measures,
sanctions, independent appeals, атомарные reputation events, контекстный
profile, rehabilitation plans/steps и рабочие очереди аудитора/арбитра.

Каждая команда требует `Idempotency-Key`; переход изменяемого объекта также
требует `expected_version`. Решение ссылается на READY evidence, точную policy
version и actor role assignment. Участник видит свой контур, cooperative roles
ограничены scope, а глобальные `AUDITOR`/`SECURITY_ADMIN` получают read-only
обзор. Активные protective measures исполняются также в identity и bounded-risk
командах, поэтому запрет роли или новой гарантии не является только UI-флагом.

Полный lifecycle, роли и доказательства: [implemented_slice_8.md](implemented_slice_8.md).
## Версионирование

- additive compatible change остаётся в `/api/v1`;
- удаление, смена смысла или типа требует `/api/v2` либо migration window;
- event schema и HTTP API версионируются независимо;
- deprecation содержит дату, замену и offline impact;
- скрытое изменение экономического правила под прежней версией запрещено.

## Реализованный solidarity API

Slice 9 публикует 25 paths в `/api/v1/solidarity`: фонды, кампании и точные bucket balances, обещания, проверенные поступления, приватные заявки, распределения, approval, delivery, жалобы, агрегированные отчёты и рабочие очереди оператора/контролёра. Все 14 команд требуют `Idempotency-Key`; переход существующего объекта дополнительно проверяет `expected_version`, а утверждение распределения - неизменяемый `allocation_hash`.

Read-модель разделяет публичные агрегаты, данные участника и staff scope. Личные evidence refs не попадают в публичные DTO, а campaign report не содержит `recipient_member_id`. Evidence вида `SOLIDARITY_AID` может загрузить активный участник с действующим назначением в кооперативе; доступ к остальным видам evidence не расширен.
## Реализованный crisis/reserves API

Slice 10 публикует 23 paths в `/api/v1/crisis`: versioned reserve targets,
append-only physical snapshots, bounded mandates и reviews, rationing
rules/previews/confirms/cancels, evidence-backed issuance, numbered paper forms,
immutable reports и рабочие очереди оператора/контролёра.

Каждая команда требует `Idempotency-Key`; transitions также проверяют
`expected_version`, а activation/approval/confirm - frozen hash. Истёкший
mandate не даёт capability. Allocation и paper form доступны самому участнику
либо scoped staff. Полный контракт: [implemented_slice_10.md](implemented_slice_10.md).

## Реализация federation API Slice 11

Префикс `/api/v1/federation` содержит 50 paths: applications, responsibility
acceptance, technical challenge, identity verification, independent audit,
activation, trust contracts, bilateral limits, bonds/exposure, incidents,
key rotations, offline epochs, package export/import/simulation/conflicts/apply,
receipts и federation paper forms. Все commands используют `Idempotency-Key`;
state transitions дополнительно требуют version/hash, evidence и независимого
actor там, где это задано доменным правилом.

OpenAPI всего содержит 226 paths. Копии backend/frontend идентичны по SHA-256
`25771D6D04143679EAF2D63EC12EACE4BE2FF7B597B948389BADAE4F500CBEE4`, а
TypeScript schema генерируется из этой спецификации.
## Operations API

- `GET /api/v1/operations/snapshot` — PII-free read model состояния локального
  узла;
- `GET /api/v1/operations/metrics` — Prometheus text exposition с bounded
  labels.

Оба endpoint требуют `COOPERATIVE_ADMIN`, `SECURITY_ADMIN` или `AUDITOR`.
Анонимный запрос получает `401`, пользователь без роли — `403`. Endpoint metrics
не является публичным `/metrics` и должен оставаться за общей auth/gateway
политикой.

## Реализация federated discovery API Slice 13

Общий OpenAPI содержит 242 paths, из них 64 относятся к
`/api/v1/federation`. Каталог публикует offers, indexes, logistics quotes,
direct/indexed/cached search, live verification, purchase intents, отдельные
goods/logistics reserves, commit/cancel и receipt read model.

`POST /api/v1/federation/peer/messages` принимает только signed envelope
`CC-PEER-1`. Входящий request связывает message, source, target, capability,
operation, fingerprint, payload hash и короткое time window. Ответ подписан
home node и связан с точным request hash. Browser API не принимает внешнюю
подпись резерва от пользователя: backend получает evidence непосредственно от
home node.

Commit и cancel являются recoverable двухфазными командами. После сохранения
`COMMITTING` или `CANCELLING` повтор использует исходный expected version,
собирает отсутствующие remote acknowledgements и только затем завершает intent.
Полный контракт и результаты проверок: [implemented_slice_13.md](implemented_slice_13.md).

## Реализация inter-node clearing API Slice 14

Общий OpenAPI содержит 254 paths. Префикс `/api/v1/federated-clearing`
публикует 12 paths: policies, obligations, cycles, полное evidence и отдельные
команды collect snapshots, prepare, proposal, collect approvals, local
approval, commit, recovery и release. Каждая command использует
`Idempotency-Key`; переходы проверяют текущее состояние, версии и hashes внутри
PostgreSQL-транзакции.

Межузловая доставка использует существующий authenticated endpoint
`POST /api/v1/federation/peer/messages` с отдельными clearing operations и
capabilities. Commit payload содержит полный набор signed prepare receipts и
approvals, поэтому lagging participant может независимо восстановить evidence,
проверить certificate и применить результат без общей БД. Canonical peer
response возвращается без повторной сериализации подписанного документа.

Точный lifecycle и трёхузловое доказательство: [implemented_slice_14.md](implemented_slice_14.md).

## Личная адресная книга

Все маршруты привязаны к `principal.member_id`; произвольный `member_id` клиент не передаёт.

```text
GET  /api/v1/participant/addresses
POST /api/v1/participant/addresses
PUT  /api/v1/participant/addresses/{address_id}
POST /api/v1/participant/addresses/{address_id}/archive
```

Команды требуют `Idempotency-Key`; изменение и архивирование также требуют
`expected_version`. Чужой адрес скрывается как `404`. В audit записываются идентификатор,
назначение, публичный код района и версия, но не точный адрес, телефон или инструкции.
Выбор записи в интерфейсе копирует значения в предложение или заказ, поэтому дальнейшее
изменение адресной книги не меняет уже созданную хозяйственную запись.
## Проверка аномалий Slice 19

Scoped API опубликован под `/api/v1/antifraud`:

```text
GET  /api/v1/antifraud/rules
GET  /api/v1/antifraud/overview
GET  /api/v1/antifraud/scans
GET  /api/v1/antifraud/signals
POST /api/v1/antifraud/scans
POST /api/v1/antifraud/signals/{signal_id}/review
POST /api/v1/antifraud/signals/{signal_id}/decision
```

Чтение разрешено `RISK_ADMIN`, `AUDITOR` и `SECURITY_ADMIN` в пределах их
cooperative scope. Запуск требует `RISK_ADMIN`; рассмотрение и решение требуют
`AUDITOR`. Все команды требуют `Idempotency-Key`, а переход сигнала также
проверяет `expected_version`. Решение принимает только `CLEARED` или
`CONFIRMED`, непустое обоснование и список READY evidence.

Ответ не содержит обвинения или итогового social score. Он возвращает версию
алгоритма и правила, тип и UUID объекта, тяжесть, действие, статус, число
наблюдений, локализуемую причину, наблюдавшиеся факты и пороги.

`GET /rules` публикует манифест алгоритма `2.0.0`: 13 requirement classes, 15
rules, SHA-256, версию синтетического regression-набора, действие каждого
правила и `production_approved=false`. Endpoint имеет ту же read-role policy,
но не требует cooperative parameter, поскольку манифест статичен для версии
приложения и не раскрывает хозяйственные данные. Детали и границы:
[implemented_slice_19.md](implemented_slice_19.md) и
[implemented_slice_21.md](implemented_slice_21.md).

## Административный реестр Slice 22

```text
GET/POST /api/v1/admin/cooperatives
POST     /api/v1/admin/cooperatives/{id}/transitions
GET/POST /api/v1/admin/members
POST     /api/v1/admin/members/{id}/transitions
GET/POST /api/v1/admin/memberships
POST     /api/v1/admin/memberships/{id}/transitions
GET/POST /api/v1/admin/users
POST     /api/v1/admin/users/{id}/transitions
```

List и overview endpoints ограничиваются cooperative scope действующих ролей.
Transition request содержит target status, reason, expected version и
idempotency key. Отключение User немедленно отзывает его active sessions;
self-disable отклоняется. `cooperative_id` при создании Member может быть опущен
только совместимым старым клиентом регистратора с ровно одним доступным scope.
## Безопасный ввод участников Slice 23

`POST /api/v1/admin/members/duplicate-check` возвращает кандидатов по точному хешу identifier и нормализованному имени в разрешённом cooperative scope. `POST /api/v1/admin/members` принимает необязательный `duplicate_resolution_code`; совпадение имени без явного решения отклоняется.

Массовый workflow использует `/api/v1/admin/imports`, `/{batch_id}/rows`, `/{batch_id}/dry-run`, `/{batch_id}/decision` и `/{batch_id}/apply`. Создание, dry run и применение требуют постоянной роли `MEMBER_REGISTRAR`; решение требует постоянной роли `DATA_STEWARD` и другого пользователя. Все команды используют `Idempotency-Key` и `expected_version`. Устаревший отчёт возвращает `MEMBER_IMPORT_PREVIEW_STALE` и не создаёт ни одной строки.
