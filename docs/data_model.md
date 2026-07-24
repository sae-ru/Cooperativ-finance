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

### `identity.participant_addresses`

`id`, `member_id`, `cooperative_id`, `label`, `purpose`, `region_code`,
`address_text`, `contact_name`, `contact_phone`, `instructions`,
`is_default_pickup`, `is_default_delivery`, `status`, timestamps, `version`.

Адресная книга приватна и читается только владельцем `member_id`. Назначение ограничено
значениями `PICKUP`, `DELIVERY`, `BOTH`; удаление переводит запись в `ARCHIVED`.
Активное имя уникально внутри участника и кооператива, а конкурентное изменение требует
`expected_version`. Точные адреса предложений, заказов и рейсов являются отдельными
неизменяемыми снимками и намеренно не ссылаются на изменяемую запись адресной книги.
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

### `exchange.deals`, `deal_terms_versions` и `deal_parties`

`deals` хранит cooperative, title, текущие `terms_version`/`terms_hash`,
status, инициатора, signed events и optimistic version. Каждая строка
`deal_terms_versions` содержит неизменяемый JSON условий и canonical SHA-256.
`deal_parties` фиксирует полный набор сторон конкретной версии и является
родительским ограничением для подтверждений и обязательств.

### `exchange.deal_confirmations`

Подтверждение связывает deal, точные version/hash, участника, пользователя,
активное назначение роли и уникальный signed event. Unique
`(deal_id, terms_version, member_id)` запрещает повторную подпись одной стороны.
Обязательства создаются только после полного required set.

### `exchange.obligations`

Хранят sequence, terms version, debtor/creditor из набора сторон, предмет,
описание качества и места, due time, единицу и точные количества
`numeric(38,12)`, правила partial/evidence/substitute, источник оценки,
liquidity class, `clearing_allowed`, status и version.

`quantity_cleared` хранит только финализированный взаимозачёт. Физическое
исполнение остаётся в `quantity_fulfilled`; эти величины не смешиваются.

CHECK:

```text
quantity_total > 0
quantity_submitted >= 0
quantity_fulfilled >= 0
quantity_cleared >= 0
quantity_submitted + quantity_fulfilled + quantity_cleared <= quantity_total
```
### `exchange.fulfillments` и `acceptance_records`

Fulfillment хранит предъявленное и принятое количество, утверждение о качестве,
место, время, исполнителя, optional logistics order, status и signed events.
Acceptance является отдельным неизменяемым решением кредитора
`ACCEPTED|PARTIALLY_ACCEPTED|REJECTED`; одна запись на fulfillment.

### `exchange.logistics_orders`

Заказ связывает obligation, назначенного carrier member, количество, маршрут,
deadlines и последовательность signed events. После `ACCEPTED` фиксируются
carrier user и role assignment; pickup и delivery может выполнить только тот
же человек. Статусы переходят `OFFERED -> ACCEPTED -> IN_TRANSIT -> DELIVERED`.

### `exchange.obligation_disputes`

Спор хранит obligation, optional fulfillment, основания, заявителя, предыдущие
статусы и open event. Решение хранит действие, пояснение, независимого
resolver, resolution event, timestamp и version. CHECK требует заполненного
resolution metadata для `RESOLVED|REJECTED`.

### `exchange.clearing_policies`

Версионированная policy кооператива хранит valuation unit, algorithm id/version,
decimal scale, rounding, minimum operation, bounds расчёта, dispute window,
required approvals, liquidity order, canonical terms/hash и две независимые
цепочки ответственности. Одновременно активна только одна policy кооператива.

### `exchange.clearing_cycles`

`id`, `cooperative_id`, `policy_id`, `cycle_code`, period, status,
`collected_count`, три canonical hash, dispute deadline, actor/event references,
timestamps и optimistic version.

### `exchange.clearing_entries`

`cycle_id`, `obligation_id`, frozen obligation version, debtor/creditor/unit,
`amount_before`, `cleared_amount`, `amount_after`, `inclusion_status`,
`exclusion_reason` и deterministic allocations.

Unique: `(cycle_id, obligation_id)`. Финальный цикл неизменяем; исправление
создаёт новый цикл или compensation cycle.

### `exchange.clearing_input_snapshots`

`id`, `cycle_id`, `input_version`, `ordered_payload`, `input_hash`,
`policy_version`, frozen actor/event и timestamp. Один snapshot на cycle;
runtime запрещено менять или удалять его.

### `exchange.clearing_positions`

`cycle_id`, `member_id`, `unit_id`, incoming/outgoing before, cleared и after,
`net_before`, `net_after`. Unique: `(cycle_id, member_id, unit_id)`.

### `exchange.clearing_approvals` и `clearing_disputes`

Approval хранит точные input/result hashes, человека, active role assignment и
signed event. Dispute хранит affected entry, reason, statement, immutable
READY evidence refs, opener, независимое решение и optimistic version.

### `exchange.clearing_proofs` и `clearing_statements`

Proof содержит полный canonical input/parameters/result, proof hash, final event
и node event hash. Statement является неизменяемой участнической выпиской;
unique действует по cycle/member/unit, а hash включён в reconciliation.

### `exchange.clearing_accounting_exports`

Один append-only draft на cycle содержит ordered payload, source event refs и
package hash. Он является мостом для утверждённого accounting mapping, но не
создаёт бухгалтерские проводки самостоятельно.
## Паи, риск и ответственность

### `risk.risk_policies`

Версионированные правила одного кооператива и denomination: индивидуальный и
групповой лимиты, глубина поручительств, canonical terms payload/hash,
инициатор, независимый утверждающий, signed event refs, status и version.
Одновременно активна не более чем одна policy на пару
`(cooperative_id, denomination)`.

### `risk.share_accounts`

`id`, `cooperative_id`, `member_id`, immutable `opening_policy_id`, `contour`,
`denomination`, `balance`, `protected_amount`, `executed_not_settled`, status,
event refs, timestamps и version.

Контуры: `PRIMARY`, `GUARANTEE`, `ROLE`, `SOLIDARITY`. Только `GUARANTEE`
покрывает direct obligation, guarantee и credit limit; только `ROLE` покрывает
role bond. База защищает `balance >= protected_amount + executed_not_settled`.

### `risk.share_contributions`

Append-only записи взноса: account, exact amount, entry type, source reference,
actor, event id и timestamp. Runtime role не имеет update/delete; изменение
дополнительно запрещено trigger.

### `risk.related_party_links`

Упорядоченная пара разных участников, relation type, source statement,
инициатор, независимое решение, event refs, status и version. Partial unique
index запрещает вторую pending/active связь той же пары.

### `risk.exposure_commitments`

Ссылка на account и действовавшую policy, владелец, тип обязательства, risk id,
стороны/role assignment, `amount_reserved`, `max_loss`, `coverage_ratio`, срок,
условия, exclusions, canonical terms/hash, личное acceptance, release и
version.

Активный резерв входит в доступный остаток счёта и aggregate exposure участника
и всей связной компоненты. Один и тот же risk id нельзя активировать повторно.

### `risk.liability_cases`

Уникальный в cooperative incident reference, commitment, ответственный,
affected amount, факты, causal graph, fault class, assessed loss, coverage
summary, rationale, appeal deadline, независимые actor/event refs и version.

Сумма assessed loss всех случаев одного commitment ограничена `max_loss`.
Текущий срез хранит assessment как `NOT_EXECUTED`: автоматического движения
пая нет.

## Trust и солидарность

Revision `0010_trust_procedural_fairness` реализует schema `trust`.

### `trust.trust_policies` и `trust.cases`

Policy хранит semantic version, canonical terms/hash, appeal/protective limits,
quorum, dual-control actors и signed event references. Case хранит уникальный
reference, subject/claimant, source, факты, требование, evidence, ответ,
процессуальные сроки, status, actor/event refs и optimistic version.

### `trust.conflict_declarations` и `trust.arbitration_decisions`

Conflict declaration неизменяема и уникальна для `(case, stage, member)`.
Decision append-only, уникально по `(case, stage, decision_round)` и содержит
outcome, standard of proof, fault, causal findings, established loss,
reasoning, consequence spec, evidence, panel snapshot и policy version.

### `trust.protective_measures`, `sanctions` и `appeals`

Protective measure имеет subject, typed scope, rationale, start/expiry/review,
lift/revoke actors и version. Sanction отдельно хранит consequence, severity,
appeal deadline и lifecycle. Appeal связывает original decision, optional
sanction, appellant, grounds/evidence, independent panel и appeal decision.

### `trust.reputation_events`

Атомарная append-only запись содержит context, classification, severity,
confidence, observation period, source events/evidence, appeal state, status,
visibility и policy version. `CORRECTION` обязана ссылаться на
`corrects_event_id`; профиль является воспроизводимой проекцией, а не таблицей
скрытого scalar score.

### `trust.rehabilitation_plans` и `rehabilitation_steps`

Plan связан с case/decision/subject, имеет сроки, проверяемые критерии и
закрывающие actor/event refs. Упорядоченный step содержит criterion, evidence и
событие завершения. История не удаляется при completion/cancellation.

Aid contribution, allocation и delivery последующего Slice 9 не используют
таблицы обязательств, кредитных позиций или reputation events.

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

## Реализованная schema solidarity

Revision `0011_solidarity_aid` создаёт `solidarity.funds`, `campaigns`, `pledges`, `contributions`, `applications`, `allocations`, `allocation_approvals`, `deliveries`, `complaints` и `campaign_reports`. Обещание не участвует в балансе; доступный остаток вычисляется только из `VERIFIED` contributions за вычетом `APPROVED`, `SUSPENDED` и `DELIVERED` allocations в том же bucket `contribution_form + unit_code`.

`allocation_approvals`, `deliveries` и `campaign_reports` append-only для runtime-role. Все хозяйственные ссылки ведут в signed journal и identity, но ни одна таблица solidarity не является обязательством, паевым резервом или reputation event.
## Реализованная schema crisis/reserves

Revision `0012_crisis_reserves` расширяет schema `solidarity` таблицами
`reserve_targets`, `reserve_snapshots`, `crisis_mandates`, `crisis_reviews`,
`rationing_rules`, `rationing_plans`, `rationing_allocations`,
`ration_issuances`, `crisis_paper_forms` и `crisis_reports`.

Targets и rules versioned; approval новой policy атомарно переводит прежнюю в
`RETIRED` при отсутствии активного использования. Snapshot хранит только
физически подтверждённое количество и evidence refs. Preview замораживает
snapshot/input/allocation hashes, confirm повторно проверяет остаток под
cooperative advisory lock. Reviews, snapshots, issuances и reports append-only;
runtime-role не имеет DELETE. Данные не являются obligation, share exposure или
reputation event.

## Реализованная federation schema Slice 11

Revision `0013_offline_nodes` создаёт schema `federation` с таблицами
`node_owner_organizations`, `external_nodes`, `node_applications`,
`node_responsible_parties`, `node_certificates`, `node_challenges`,
`node_trust_contracts`, `node_bilateral_limits`, `node_bonds`, `node_exposures`,
`offline_epochs`, `sync_packages`, `inbox_events`, `sync_conflicts`,
`sync_receipts`, `federation_checkpoints`, `node_security_incidents` и
`node_key_rotation_requests`. Revision `0014_federation_paper_forms` добавляет
`paper_forms`.

Identifiers, hashes, limits, periods, states и actor/event links ограничены
FK/CHECK/UNIQUE constraints. Принятые package/event/receipt evidence и бумажные
оригиналы защищены append-only triggers. Runtime role не имеет DELETE; downgrade
guard отказывается удалять непустой контур.

## Реализованная schema federated discovery Slice 13

Revision `0015_federated_discovery` создаёт `federated_offers`,
`offer_index_snapshots`, `logistics_quotes`, `purchase_intents` и
`reservation_receipts`. Revision `0016_peer_protocol` добавляет
`peer_protocol_exchanges`. Revision `0017_peer_reservations` добавляет
`peer_resource_reservations` и signed evidence для recoverable commit/cancel.

Home-node hold уникален по `(buyer_node_id, buyer_intent_id, kind)`, ссылается
на стабильную версию offer или quote, хранит amount/unit, capability,
exposure, summary hash, receipt и отдельные commit/release artifacts. Активная
ёмкость вычисляется под DB lock одновременно по локальным и удалённым
удержаниям. Receipt/event evidence append-only; populated downgrade запрещён.

## Реализованная schema межузлового клиринга Slice 14

Revision `0018_inter_node_clearing` добавляет политики, obligations, cycles,
snapshots, prepare receipts, proposals, approvals, certificates, entries,
node positions, apply receipts и reconciliation proofs. Все значимые artifacts
хранят canonical payload, hash, signature, signer fingerprint, event link и
временную границу там, где она применима.

Уникальные ограничения не допускают два локальных apply одного certificate,
повторную локальную подпись узла и неоднозначную версию obligation. Prepare и
apply меняют остатки и `NodeExposure` под row/advisory locks. Signed evidence и
финальные записи защищены append-only triggers; runtime role не имеет DELETE, а
populated downgrade заблокирован.
## Реализованная schema кабинета участника Slice 18

Revisions `0019_exchange_participant` и `0020_purchase_deal_bridge` связывают
обычного участника, предложение, подтверждённое намерение обмена, локальную
сделку и обязательства. Revision `0021_logistics_contacts` добавляет приватные
снимки точек забора и доставки в предложение, заказ и рейс. Revision
`0022_participant_addresses` добавляет версионируемую личную адресную книгу со
статусом архивирования и основными точками забора и доставки.

Адресная книга является удобным источником заполнения, но не изменяемой ссылкой
сделки. Хозяйственная запись хранит собственный снимок точного адреса, контакта и
инструкций. Общий каталог получает только публичный код района.