# Каталог событий

Статус: нормативный каталог; реализованные события Slices 1-7 проверяются signed-journal тестами, точные DTO публикуются вместе с модулями.

## Правила именования

- формат: `<context>.<noun>_<past_tense>`;
- имя описывает свершившийся факт, а не команду;
- `schema_version` изменяется при несовместимой структуре;
- payload содержит только данные факта и ссылки, не произвольный снимок БД;
- персональные данные минимизируются.

## Identity

| Событие | Минимальный payload |
|---|---|
| `identity.member_registered` | member, cooperative, member_type |
| `identity.membership_activated` | membership, period |
| `identity.role_assigned` | member, role, scope, period, assigners |
| `identity.role_revoked` | assignment, reason, decision |
| `identity.session_revoked` | user, session, reason |
| `identity.access_recovered` | user, approvers, procedure |
| `identity.member_verified` | member, verifier, evidence, decision |
| `identity.member_suspended` | member, scope, reason, decision |
| `identity.member_exit_started` | member, obligations_snapshot, decision |
| `identity.duplicate_merge_requested` | source, survivor, safe evidence refs, expiry |
| `identity.duplicate_merge_blocked` | source, survivor, blocker summary, decision stage |
| `identity.duplicate_merge_decided` | source, survivor, reviewer, immutable mapping |
| `identity.duplicate_merge_rejected` | source, survivor, reviewer, reason |
| `identity.duplicate_merge_expired` | source, survivor, expiry/supersession reason |
| `identity.service_client_registered` | client, owner, scopes, expiry |

## Assets

| Событие | Минимальный payload |
|---|---|
| `catalog.unit_created` | unit, code, dimension, decimal scale |
| `catalog.product_created` | product, sku, unit, tolerance, evidence policy |
| `inventory.warehouse_created` | warehouse, address, storage conditions |
| `evidence.upload_intent_created` | evidence, hash, size, MIME, retention |
| `evidence.blob_stored` | evidence, hash, size, encryption profile |
| `inventory.lot_registered` | lot, product, quantity, warehouse, owner |
| `inventory.lot_attested` | lot, attester, measurements, evidence |
| `inventory.lot_verified` | lot, quality decision, valid quantity |
| `inventory.quantity_reserved` | lot, purpose, quantity, expiry |
| `inventory.quantity_released` | reservation, quantity, reason |
| `inventory.discrepancy_recorded` | lot, expected, actual, evidence |
| `rights.commodity_right_issued` | right, lot, owner, quantity, reservation |
| `rights.commodity_right_transferred` | right, from, to, quantity |
| `rights.commodity_right_redemption_requested` | right, owner, warehouse, quantity |
| `rights.commodity_right_redeemed` | right, quantity, fulfillment |
| `rights.commodity_right_frozen` | right, reason, decision |
| `rights.commodity_right_unfrozen` | right, restored status, decision |

## Exchange

| Событие | Минимальный payload |
|---|---|
| `deals.deal_proposed` | deal, parties, terms_version, terms_hash |
| `deals.deal_terms_revised` | deal, old/new version, terms_hash, parties |
| `deals.party_confirmed` | deal, party, terms_version, terms_hash |
| `deals.deal_confirmed` | deal, confirmations, terms_version, obligations |
| `obligations.obligation_created` | deal, debtor, creditor, subject, quantity, due |
| `obligations.fulfillment_recorded` | obligation, quantity, performer, evidence |
| `obligations.fulfillment_accepted` | fulfillment, accepted_quantity, receiver, evidence |
| `obligations.obligation_disputed` | obligation, fulfillment, claimant, grounds, evidence |
| `disputes.obligation_dispute_resolved` | dispute, action, resolver, evidence, resulting status |
| `obligations.obligation_overdue` | obligation, due, effective time |
| `obligations.overdue_scan_completed` | cooperative, effective time, marked ids |
| `logistics.order_offered` | obligation, carrier, quantity, route, deadlines |
| `logistics.order_accepted` | order, carrier, from/to status |
| `logistics.order_picked_up` | order, carrier, evidence, from/to status |
| `logistics.order_delivered` | order, carrier, evidence, from/to status |
| `clearing.policy_proposed` | policy, version, parameters, terms_hash, proposer |
| `clearing.policy_superseded` | policy, replacing policy, previous status |
| `clearing.policy_approved` | policy, terms_hash, independent approver |
| `clearing.cycle_created` | cycle, policy, period, creator |
| `clearing.input_collected` | cycle, eligible count, from/to status |
| `clearing.input_frozen` | cycle, input_hash, policy version, obligation versions |
| `clearing.preview_created` | cycle, input/parameters/result hashes, totals |
| `clearing.preview_approved` | cycle, input/result hashes, independent approver |
| `clearing.dispute_opened` | cycle, entry, reason, evidence, opener |
| `clearing.dispute_decided` | dispute, outcome, independent resolver, notes |
| `clearing.cycle_ready` | cycle, result hash, approvals, dispute deadline |
| `obligations.obligation_cleared` | cycle, obligation, before/cleared/after, versions |
| `clearing.cycle_finalized` | cycle, proof hash, result hash, entry count |
| `clearing.statement_created` | cycle, member, unit, statement hash |
| `clearing.cycle_reconciled` | cycle, proof, statement hashes, accounting package |
## Risk and responsibility

| Событие | Минимальный payload |
|---|---|
| `risk.policy_proposed` | policy, version, denomination, limits, terms_hash, evidence |
| `risk.policy_superseded` | policy, replacing_policy, previous status |
| `risk.policy_approved` | policy, terms_hash, approver, evidence |
| `shares.account_opened` | account, member, policy/hash, contour, balances, evidence |
| `shares.contribution_recorded` | account, amount, source, resulting balance, evidence |
| `risk.related_party_link_proposed` | pair, relation type, statement, evidence |
| `risk.related_party_link_approved` | link, decision, merged group |
| `risk.related_party_link_rejected` | link, decision, evidence |
| `shares.exposure_proposed` | account, risk, exact terms/hash, preview |
| `shares.exposure_reserved` | commitment, owner acceptance, amount, max_loss |
| `shares.exposure_cancelled` | commitment, reason, evidence |
| `shares.exposure_released` | commitment, reason, evidence |
| `liability.case_opened` | case, incident, facts, causal graph, evidence |
| `liability.assessment_recorded` | case, fault class, loss, coverage, appeal, evidence |
| `risk.antifraud_signal_detected` | scan, rule/version, subject, severity, action, observed facts, thresholds |
| `risk.antifraud_signal_reobserved` | signal, scan, subject, occurrence count, observed time |
| `risk.antifraud_scan_completed` | cooperative, algorithm, cutoff/lookback, counts, automatic decisions |
| `risk.antifraud_review_started` | signal, reviewer role, exact version, subject |
| `risk.antifraud_signal_decided` | signal, CLEARED/CONFIRMED, rationale, evidence, automation release |
| `responsibility.assignment_started` | person, role, subject, scope, exposure |
| `responsibility.custody_offered` | subject, from, to |
| `responsibility.custody_accepted` | subject, from, to, evidence |

Событие исполнения coverage намеренно отсутствует в Slice 6: assessment не
двигает пай автоматически.

## Trust

| Событие | Минимальный payload |
|---|---|
| `trust.policy_proposed` | policy, semantic version, terms hash, proposer |
| `trust.policy_approved` | policy, exact hash, independent approver |
| `trust.policy_superseded` | previous policy, replacement policy |
| `disputes.dispute_opened` | case, subject, claimant, source, evidence |
| `disputes.response_recorded` | case, respondent, response, evidence |
| `disputes.case_ready_for_decision` | case, auditor, review note |
| `disputes.conflict_declared` | case, stage, member, assessment, reason |
| `disputes.decision_issued` | case, stage, panel, outcome, causal findings, evidence |
| `sanctions.protective_measure_imposed` | case, subject, type, scope, expiry/review |
| `sanctions.protective_measure_lifted` | measure, actor, reason |
| `sanctions.protective_measure_revoked` | measure, appeal, reason |
| `sanctions.sanction_proposed` | decision, subject, measure, severity, appeal window |
| `sanctions.sanction_finalized` | sanction, finalizer, policy |
| `sanctions.sanction_revoked` | sanction, appeal decision, reason |
| `appeals.appeal_submitted` | case, decision, appellant, grounds, evidence |
| `appeals.appeal_decided` | appeal, independent panel, outcome, correction actions |
| `reputation.event_recorded` | context, subject, source events, classification |
| `reputation.event_activated` | reputation event, final decision |
| `reputation.event_corrected` | original event, correction event, appeal |
| `reputation.rehabilitation_recorded` | subject, context, completed plan |
| `rehabilitation.plan_created` | case, decision, subject, criteria, steps |
| `rehabilitation.step_completed` | plan, step, evidence |
| `rehabilitation.plan_completed` | plan, verifier, context |
| `rehabilitation.plan_cancelled` | plan, appeal/correction, reason |

## Solidarity and crisis

| Событие | Минимальный payload |
|---|---|
| `solidarity.campaign_opened` | fund, purpose, rules, period |
| `solidarity.contribution_verified` | campaign, kind, quantity, verifier |
| `solidarity.allocation_approved` | campaign, recipient_ref, rule, approvers |
| `solidarity.aid_delivered` | allocation, delivery proof, witness |
| `solidarity.campaign_closed` | campaign, totals, residue_rule |
| `crisis.reserve_snapshot_recorded` | target, verified, available, level, snapshot_hash |
| `crisis.mandate_activated` | policy_version, mandate, scope, period |
| `crisis.rationing_confirmed` | resource, rule_version, allocations_hash |
| `crisis.mandate_closed` | mandate, reconciliation, report_hash, safe_state |

## Node and security

| Событие | Минимальный payload |
|---|---|
| `node.registered` | node, organization, keys, permissions |
| `node.application_submitted` | node, owner, sponsor, requested capabilities |
| `node.challenge_completed` | node, challenge, keys, release, receipt |
| `node.trust_contract_issued` | node, capabilities, limits, expiry, parties |
| `node.responsibility_assigned` | node, person, role, scope, period, bond |
| `node.bond_reserved` | node, owner/role, account, max_loss, period |
| `node.activated_limited` | node, limits, approvers |
| `node.activated` | node, trust level, contract |
| `node.suspended` | node, reason, restrictions, decision |
| `node.rehabilitated` | node, audit, new limits, decision |
| `node.key_rotated` | node, old_key, new_key, effective_at |
| `node.key_revoked` | key, reason, effective_at, approvers |
| `node.quarantined` | node, incident, restrictions |
| `offline.epoch_opened` | node, epoch, limits, policy_version |
| `offline.package_applied` | source, package, event range, result |
| `offline.conflict_recorded` | package, objects, conflict_class |
| `offline.conflict_resolved` | conflict, decision, compensations |
| `security.incident_opened` | type, scope, severity, reporter |
| `protocol.update_accepted` | old_version, new_version, package_hash |

## Federation discovery and clearing

| Событие | Минимальный payload |
|---|---|
| `federation.offer_published` | offer, version, home node, availability, price, expiry |
| `federation.offer_revoked` | offer, version, reason, publisher |
| `federation.logistics_quote_issued` | route, capacity, cost components, liability, expiry |
| `federation.purchase_intent_created` | buyer, offer/quote versions, quantity, destination |
| `federation.goods_reserved` | intent, seller node, reservation, quantity, expiry |
| `federation.logistics_reserved` | intent, logistics node, capacity, expiry |
| `federation.purchase_committed` | intent, canonical summary, receipts |
| `federation.purchase_compensated` | intent, released reservations, reason |
| `federated_clearing.snapshot_signed` | cycle, node, obligations/positions hash |
| `federated_clearing.node_prepared` | cycle, node, reservations, exposure, expiry |
| `federated_clearing.proposal_created` | cycle, input/result hashes, prepare receipts |
| `federated_clearing.node_approved` | cycle, node, result hash, approvers |
| `federated_clearing.commit_certified` | cycle, affected nodes, approvals, certificate hash |
| `federated_clearing.node_applied` | cycle, node, entries hash, local events |
| `federated_clearing.reconciled` | cycle, apply receipts, proof hash |
## Consumers

Обязательные синхронные consumers находятся внутри orchestrating transaction.
Асинхронные consumers ограничены projections, alerts, reports, package builder
и audit analytics. Consumer обязан быть идемпотентным по `event_id`.

## Реализованные события solidarity

| Event type | Назначение |
|---|---|
| `solidarity.fund_proposed` | предложение правил отдельного фонда |
| `solidarity.fund_approved` | независимое утверждение фонда |
| `solidarity.campaign_created` | draft кампании и hash условий |
| `solidarity.campaign_opened` | открытие утверждённой кампании |
| `solidarity.pledge_recorded` | добровольное обещание, не являющееся активом |
| `solidarity.contribution_received` | физически принятое поступление |
| `solidarity.contribution_verified` | независимая проверка и включение в bucket |
| `solidarity.contribution_rejected` | отклонение непроверенного поступления |
| `solidarity.application_submitted` | приватная заявка участника |
| `solidarity.application_eligible` | независимое подтверждение допустимости |
| `solidarity.application_rejected` | мотивированное отклонение заявки |
| `solidarity.allocation_proposed` | предложение распределения по policy hash |
| `solidarity.allocation_approved` | независимое резервирование остатка |
| `solidarity.allocation_rejected` | отклонение распределения |
| `solidarity.aid_delivered` | подтверждённая передача помощи |
| `solidarity.complaint_opened` | жалоба и приостановка невыданного allocation |
| `solidarity.complaint_resolved` | независимое исправляющее решение |
| `solidarity.complaint_rejected` | мотивированное отклонение жалобы |
| `solidarity.campaign_closed` | reconciliation и агрегированный immutable report |
## Реализованные события crisis/reserves

| Event type | Назначение |
|---|---|
| `crisis.reserve_target_proposed` | новая version нормативов критического ресурса |
| `crisis.reserve_target_retired` | подписанная атомарная замена прежней policy |
| `crisis.reserve_target_approved` | независимое утверждение target |
| `crisis.reserve_snapshot_recorded` | physical verified append-only snapshot |
| `crisis.mandate_proposed` | evidence, scope, capabilities, bounds и safe state |
| `crisis.mandate_activated` | независимая activation ограниченной власти |
| `crisis.mandate_reviewed` | facts и явное continue/extend решение |
| `crisis.rationing_rule_proposed` | versioned eligibility/formula/limits |
| `crisis.rationing_rule_retired` | замена правила после reconciliation |
| `crisis.rationing_rule_approved` | независимый approval terms hash |
| `crisis.rationing_previewed` | frozen snapshot, input и allocations hashes |
| `crisis.rationing_confirmed` | повторная проверка и точное резервирование |
| `crisis.rationing_cancelled` | освобождение невыданного назначения |
| `crisis.ration_issued` | evidence-backed выдача без создания долга |
| `crisis.paper_form_issued` | нумерованная форма с checksum и expiry |
| `crisis.paper_form_recorded` | независимый ввод canonical payload |
| `crisis.mandate_closed` | reconciliation и immutable report |
| `crisis.mandate_expired` | принудительный выход в safe state и report |

## Federation events Slice 11

| Event type | Назначение |
|---|---|
| `federation.node_application_created/submitted` | заявка внешнего узла |
| `federation.node_responsibility_accepted` | личное принятие именованной роли |
| `federation.node_challenge_issued/passed` | техническое доказательство владения |
| `federation.node_audit_approved` | независимое решение onboarding |
| `federation.trust_contract_proposed/activated` | ограниченный trust contract |
| `federation.bilateral_limit_proposed/activated` | лимит exposure |
| `federation.node_bond_activated` | обеспечение ответственности узла |
| `federation.offline_epoch_opened/closed` | временная offline-граница |
| `federation.sync_package_exported/simulated/applied` | перенос и применение пакета |
| `federation.sync_conflict_opened/resolved` | конфликт без удаления истории |
| `federation.node_incident_opened/resolved` | quarantine lifecycle |
| `federation.node_key_rotation_requested/rotated` | dual-control ротация ключа |
| `federation.paper_form_issued` | выдача нумерованного оригинала |
| `federation.paper_operation_recorded` | независимый ввод операции |
| `federation.paper_form_voided` | контролируемое аннулирование оригинала |

Все события входят в общий signed journal и node hash-chain. Импортированный
payload сохраняет source package/checkpoint; локальное решение о его применении
является отдельным локально подписанным событием.

## События federated discovery Slice 13

| Event type | Назначение |
|---|---|
| `federation.offer_published/revoked` | versioned lifecycle предложения |
| `federation.offer_index_published` | подписанный ordered index home node |
| `federation.logistics_quote_issued` | маршрут, capacity, стоимость и ответственность |
| `federation.purchase_intent_created` | выбор точных offer/quote versions и summary |
| `federation.goods_reserved/logistics_reserved` | buyer-side проверенный receipt |
| `federation.purchase_commit_requested` | durable начало commit saga |
| `federation.purchase_cancellation_requested` | durable начало compensation saga |
| `federation.purchase_committed/compensated` | завершение после всех remote evidence |
| `federation.purchase_intent_expired` | автоматическое истечение незавершённого intent |
| `federation.peer_resource_reserved` | home-node hold и reserved exposure |
| `federation.peer_resource_committed` | home-node commit и current exposure |
| `federation.peer_resource_released/expired` | освобождение либо истечение hold |

## События межузлового клиринга Slice 14

Реализация использует namespace `federation.*`: policy activated, inter-node
obligation confirmed, cycle created, snapshot signed/accepted, node prepared,
prepare receipt accepted, proposal signed/verified, node approved, approval
accepted, commit certified, certificate accepted/applied, prepare
expired/released, apply receipt accepted и clearing reconciled.

Локальное принятие чужого signed artifact и локальное хозяйственное применение
являются разными событиями. Это позволяет доказать, какой узел создал документ,
кто его принял и когда именно изменилось локальное состояние.

## События локальной безопасности Slice 20

| Event type | Назначение |
|---|---|
| `identity.account_recovery_requested` | персональный requester, target, reason, expiry и evidence заявки |
| `identity.account_recovery_executed` | независимое решение, отзыв сессий/TOTP и обязательная смена пароля |
| `identity.account_recovery_rejected` | мотивированное независимое отклонение |
| `identity.break_glass_requested` | target, allowlisted role, scope, requested duration и evidence |
| `identity.break_glass_activated` | независимый approver и точный момент expiry временной власти |
| `identity.break_glass_rejected` | мотивированное отклонение аварийной власти |
| `identity.break_glass_revoked` | досрочный отзыв, revoker и причина |

Все события подписывает node key, они входят в общую hash-chain и outbox.
Открытый/временный пароль и TOTP seed не входят ни в payload, ни в evidence.
Обычный audit дополнительно связывает событие с HTTP request id; каждое
фактическое использование active break-glass создаёт `BREAK_GLASS_ACCESS_USED`.
## События внешних интеграций Slice 24

| Event type | Назначение |
|---|---|
| `identity.service_client_change_requested` | owner, operation, безопасная policy summary, срок заявки и персональный requester |
| `identity.service_client_registered` | independently approved client, owner, scopes, network policy, expiry и credential id без secret |
| `identity.service_client_policy_updated` | independently approved новая policy и новая version |
| `identity.service_client_credential_rotated` | retirement прежнего credential, новый credential id и отзыв machine tokens без secret |
| `identity.service_client_reactivated` | independently approved возврат suspended client в active state |
| `identity.service_client_change_rejected` | независимое мотивированное отклонение заявки |
| `identity.service_client_suspended` | немедленная защитная остановка и revocation действующих tokens |
| `identity.service_client_revoked` | безвозвратный отзыв client, credentials и tokens |

Lifecycle events подписываются node key и входят в общую hash-chain. Technical
contact PII, credential secret, token и точный открытый authentication material
не включаются в signed payload. Успешная и неуспешная machine authentication,
token issuance и rate/network отказ дополнительно попадают в bounded audit с
request id; эти записи не подменяют подписанное решение человека.