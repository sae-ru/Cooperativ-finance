# Модель данных

Статус: обязательные правила проектирования PostgreSQL.

## Общие соглашения

- PostgreSQL 18.x, резервно 17.x после тестов;
- идентификаторы: UUID, генерируются приложением;
- время: `timestamptz` в UTC;
- количества и оценки: `numeric(38, 12)` с отдельной единицей и допустимой
  точностью;
- деньги не смешиваются с клиринговой оценочной единицей;
- enum, влияющий на протокол, хранится как text с CHECK либо справочником;
- все таблицы имеют `created_at`; изменяемые агрегаты имеют `updated_at` и
  `version` для optimistic concurrency;
- экономические записи используют soft state transitions, но не общий
  безусловный `deleted_at`;
- персональные данные отделены от доказательных хозяйственных полей.

## Схемы PostgreSQL

| Schema | Назначение |
|---|---|
| `identity` | пользователи, участники, роли, сессии |
| `assets` | каталог, склад, качество, права |
| `exchange` | сделки, обязательства, исполнения, клиринг |
| `risk` | паи, лимиты, поручительства, ответственность |
| `trust` | споры, санкции, репутационные события |
| `solidarity` | фонды, помощь, резервы, кризис |
| `node` | узлы, ключи, epochs, sync |
| `journal` | signed events, audit, outbox/inbox |
| `reporting` | перестраиваемые projections/materialized views |

Приложение использует одного владельца миграций и отдельную runtime-роль без
права DROP/ALTER. По возможности модульная runtime-роль получает запись только
в свою схему.

## Общие таблицы идентификации

### `identity.users`

`id`, `login_normalized`, `password_hash`, `status`, `failed_attempts`,
`locked_until`, `last_login_at`, timestamps, version.

Ограничения: уникальный нормализованный login; password hash никогда не
попадает в API, audit payload или backup manifest.

### `identity.members`

`id`, `member_type`, `display_name`, `pii_record_id`, `status`, timestamps.

PII хранится в отдельной таблице с более строгими правами и собственным сроком
хранения. Хозяйственные события ссылаются на стабильный `member_id`.

### `identity.role_assignments`

`id`, `member_id`, `cooperative_id`, `role_code`, `scope_type`, `scope_id`,
`valid_from`, `valid_until`, `status`, `assigned_by`, `revoked_by`, version.

Активность роли проверяется запросом по времени операции. Историческая роль не
удаляется.

## Склад и товарные права

### `assets.inventory_lots`

`id`, `product_id`, `warehouse_id`, `owner_member_id`, `quantity_total`,
`quantity_scale`, `unit_id`, `quality_status`, `lot_status`, `expires_at`,
`custodian_assignment_id`, version.

### `assets.lot_balances`

`lot_id`, `available_qty`, `reserved_qty`, `rights_issued_qty`,
`quarantined_qty`, version.

CHECK: все значения неотрицательны; сумма использованных контуров не превышает
подтверждённое количество с учётом документированных потерь.

### `assets.inventory_reservations`

`id`, `lot_id`, `purpose_type`, `purpose_id`, `quantity`, `status`,
`expires_at`, `created_event_id`, `released_event_id`.

Уникальность активного резервирования определяется его purpose. Освобождение
создаёт событие и не удаляет запись.

### `assets.commodity_rights`

`id`, `lot_id`, `owner_member_id`, `quantity`, `unit_id`, `status`,
`redeem_location`, `valid_until`, `reservation_id`, version.

Погашение защищено unique записью операции и условным переходом состояния.

## Сделки, обязательства и клиринг

### `exchange.deals`

`id`, `cooperative_id`, `terms_version`, `terms_hash`, `status`,
`proposed_by`, `confirmed_at`, version.

### `exchange.obligations`

`id`, `deal_id`, `debtor_id`, `creditor_id`, `subject_type`, `subject_id`,
`quantity_total`, `quantity_fulfilled`, `unit_id`, `due_at`, `status`,
`liquidity_class`, `valuation_version`, version.

CHECK: `0 <= quantity_fulfilled <= quantity_total`.

### `exchange.fulfillments`

`id`, `obligation_id`, `quantity`, `accepted_quantity`, `quality_status`,
`performed_at`, `status`, `custody_transfer_id`, `event_id`.

### `exchange.clearing_cycles`

`id`, `algorithm_id`, `algorithm_version`, `input_hash`, `parameters_hash`,
`status`, `previewed_at`, `dispute_until`, `finalized_at`, version.

### `exchange.clearing_entries`

`cycle_id`, `obligation_id`, `amount_before`, `cleared_amount`,
`amount_after`, `exclusion_reason`.

Unique: `(cycle_id, obligation_id)`. Финальный цикл неизменяем; исправление
создаёт новый цикл или compensation cycle.

### `exchange.clearing_input_snapshots`

`id`, `cycle_id`, `input_version`, `ordered_payload`, `input_hash`,
`policy_version`, `created_by`, `frozen_at`.

Snapshot immutable после freeze. Новая версия не обновляет старую.

### `exchange.clearing_positions`

`cycle_id`, `member_id`, `unit_id`, `incoming_before`, `outgoing_before`,
`net_before`, `cleared`, `net_after`, `credit_exposure`, `status`.

### `exchange.clearing_approvals` и `clearing_disputes`

Approval хранит cycle/input/result hashes, человека, роль, scope и подпись.
Dispute хранит affected entry, grounds, evidence, temporary exclusion и
decision. Unique запрещает две подписи одной required role тем же человеком.

### `exchange.clearing_proofs` и `clearing_statements`

Proof immutable и связан с final event. Statement является версионированной
участнической выпиской, hash которой входит в reconciliation.
## Паи, риск и ответственность

### `risk.share_accounts`

`id`, `member_id`, `cooperative_id`, `account_type`, `balance`,
`protected_amount`, `currency_or_unit`, status, version.

Типы минимум: `PRIMARY`, `GUARANTEE`, `ROLE`, `INFRASTRUCTURE`, `SOLIDARITY`.
Солидарный контур не используется как обеспечение.

### `risk.share_reservations`

`id`, `share_account_id`, `risk_type`, `risk_id`, `amount`, `status`,
`max_loss`, `expires_at`, `priority`, `created_event_id`, `released_event_id`.

База запрещает отрицательный доступный баланс и повторную active reservation
для одного risk id.

### `risk.guarantees`

`id`, `guarantor_id`, `beneficiary_id`, `subject_id`, `amount_limit`,
`used_amount`, `valid_from`, `valid_until`, `status`, `share_reservation_id`.

### `risk.responsibility_assignments`

`id`, `member_id`, `organization_id`, `role_assignment_id`, `subject_type`,
`subject_id`, `scope`, `max_exposure`, `valid_from`, `valid_until`, `status`.

### `risk.custody_transfers`

`id`, `subject_type`, `subject_id`, `from_assignment_id`, `to_assignment_id`,
`offered_at`, `accepted_at`, `status`, `event_id`.

Только статус `ACCEPTED` закрывает предыдущую сохранность.

## Trust и солидарность

Репутация хранит атомарные `reputation_events`; профиль является проекцией.
Sanction, appeal и rehabilitation имеют отдельные таблицы и независимые
decision records. Aid contribution, allocation и delivery не используют
таблицы обязательств или кредитных позиций.

## Федеративный каталог и межузловой клиринг

### `federation.offers` и `offer_index_snapshots`

Offer хранит global id, version, home node, product/unit/quality mapping,
published quantity/band, base price, availability, conditions, expiry, payload
hash и signatures. Snapshot хранит ordered offer hashes и publisher checkpoint.

### `federation.logistics_quotes`

`id`, route request hash, logistics node, route legs, capacity, cost components,
valuation, delivery window, liability limit, bond ref, assumptions, expiry,
payload hash и signatures.

### `federation.purchase_intents` и reservations

Intent хранит buyer, offer/quote versions, quantity, destination, landed-cost
summary hash и state. Goods/logistics reservation receipts хранят home node,
resource, amount/capacity, expiry, status и compensating event.

### `federation.clearing_cycles` и node snapshots

Cycle хранит algorithm/policy, input/result hashes и state. Node snapshot
содержит signed obligations/position payload, checkpoint и disclosure scope.

### `federation.prepare_receipts` и approvals

Prepare receipt хранит node, input hash, local reservations, maximum exposure,
expiry и signatures. Approval хранит node, result hash, approvers и signature.

### `federation.commit_certificates` и apply receipts

Certificate immutable, уникален по cycle/result hash и содержит approvals всех
affected nodes. Apply receipt уникален по `(certificate_id, node_id)` и хранит
local entries/events hash. Reconciliation проверяет полный required set.
## Администрирование и доверие узлов

### Identity administration

- `identity.cooperatives`: организация и status;
- `identity.memberships`: member/cooperative, level, period, exit state;
- `identity.service_clients`: owner, scopes, credential refs, limits, expiry;
- `identity.member_merge_cases`: duplicate candidates, evidence, decision, id map;
- `identity.recovery_cases`: account, approvers, reason, status, event.

### `node.node_applications`

`id`, `proposed_node_id`, `owner_organization_id`, `sponsor_id`, requested
capabilities/limits, public keys, release/protocol/policy versions, responsible
people, evidence refs, status, version.

### `node.nodes` и `node.responsibility_records`

Node хранит status/trust level/current contract. Responsibility record хранит
owner, technical custodian, security administrator, business operator,
auditor, role assignments, period, scope и bond refs. Историческая запись
неизменяема после окончания периода.

### `node.trust_contracts` и `node.bilateral_limits`

Contract хранит capabilities, data scopes, versions, SLA, expiry и revocation
conditions. Bilateral limit уникален по peer/capability/policy period и содержит
maximum package, unsettled exposure, clearing position и offline duration.

### `node.node_bonds`

Связывает отдельный share reservation/guarantee с node, owner/role, capability,
period и max loss. Основные паи обычных участников напрямую не используются.

### `node.node_challenges` и `node.node_audits`

Challenge хранит nonce/hash, expiry, response signatures и test receipt.
Audit хранит scope, evidence, findings, decision и срок следующей проверки.
## Журнал и интеграционный контур

### `journal.signed_events`

`event_id`, `node_id`, `local_sequence`, `event_type`, `schema_version`,
`aggregate_type`, `aggregate_id`, `payload_json`, `payload_hash`,
`previous_event_hash`, `occurred_at`, `recorded_at`, `actor_json`,
`signatures_json`, `protocol_version`.

Unique: `event_id`; `(node_id, local_sequence)`; при необходимости
`(aggregate_type, aggregate_id, aggregate_version)`.

UPDATE и DELETE runtime-роли запрещены.

### `journal.outbox`

`id`, `event_id`, `topic`, `payload_json`, `available_at`, `attempts`,
`locked_at`, `processed_at`, `last_error`, `status`.

### `journal.inbox`

`source_node_id`, `message_id`, `payload_hash`, `received_at`, `status`,
`applied_event_ids`, `error_code`.

Unique `(source_node_id, message_id)` обеспечивает replay protection.

## Индексы

- все foreign keys индексируются;
- очереди индексируются по `(status, available_at)`;
- обязательства по `(debtor_id, status, due_at)` и `(creditor_id, status)`;
- остатки и права по складу, товару и статусу;
- события по aggregate, actor, node sequence и времени;
- частичные индексы используются для active reservations и unresolved cases;
- JSONB индексируется только под измеренный запрос.

## Миграции

- одна Alembic head в основной ветке;
- миграция содержит upgrade, проверенный downgrade либо явный recovery plan;
- destructive change проходит expand/migrate/contract;
- изменение precision, unit или enum имеет отдельную проверку данных;
- миграция не переписывает подписанный payload;
- перед релизом миграция тестируется на копии объёма, превышающего пилотный.
