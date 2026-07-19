# Каталог событий

Статус: исходный каталог; точные JSON Schemas создаются вместе с модулями.

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
| `identity.duplicate_merge_decided` | source ids, surviving id, reviewer, mapping |
| `identity.service_client_registered` | client, owner, scopes, expiry |

## Assets

| Событие | Минимальный payload |
|---|---|
| `inventory.lot_registered` | lot, product, quantity, warehouse, owner |
| `inventory.lot_attested` | lot, attester, measurements, evidence |
| `inventory.lot_verified` | lot, quality decision, valid quantity |
| `inventory.quantity_reserved` | lot, purpose, quantity, expiry |
| `inventory.quantity_released` | reservation, quantity, reason |
| `inventory.discrepancy_recorded` | lot, expected, actual, evidence |
| `rights.commodity_right_issued` | right, lot, owner, quantity, reservation |
| `rights.commodity_right_transferred` | right, from, to, quantity |
| `rights.commodity_right_redeemed` | right, quantity, fulfillment |
| `rights.commodity_right_frozen` | right, reason, decision |

## Exchange

| Событие | Минимальный payload |
|---|---|
| `deals.deal_proposed` | deal, parties, terms_hash |
| `deals.deal_confirmed` | deal, confirmations, terms_version |
| `obligations.obligation_created` | debtor, creditor, subject, quantity, due |
| `obligations.fulfillment_recorded` | obligation, quantity, performer, evidence |
| `obligations.fulfillment_accepted` | fulfillment, accepted_quantity, receiver |
| `obligations.obligation_disputed` | obligation, claimant, grounds |
| `obligations.obligation_defaulted` | obligation, decision, remaining |
| `logistics.custody_transferred` | subject, from, to, place, evidence |
| `clearing.cycle_previewed` | cycle, algorithm, input_hash, result_hash |
| `clearing.cycle_disputed` | cycle, entry, grounds |
| `clearing.cycle_finalized` | cycle, proof_hash, entries_hash |
| `clearing.input_frozen` | cycle, snapshot_hash, obligations_versions |
| `clearing.cycle_approved` | cycle, summary_hash, role, approver |
| `clearing.cycle_reconciled` | cycle, proof, statements, accounting refs |

## Risk and responsibility

| Событие | Минимальный payload |
|---|---|
| `shares.contribution_recorded` | account, amount, source, contour |
| `shares.exposure_reserved` | account, risk, amount, max_loss, expiry |
| `shares.exposure_released` | reservation, amount, reason |
| `guarantees.guarantee_accepted` | guarantor, subject, limit, reservation |
| `guarantees.guarantee_called` | guarantee, assessed_loss, decision |
| `liability.assessment_finalized` | case, causal_links, fault_class, coverage |
| `liability.coverage_executed` | assessment, layer, amount, event refs |
| `responsibility.assignment_started` | person, role, subject, scope, exposure |
| `responsibility.custody_offered` | subject, from, to |
| `responsibility.custody_accepted` | subject, from, to, evidence |

## Trust

| Событие | Минимальный payload |
|---|---|
| `disputes.dispute_opened` | subject, claimant, grounds, evidence |
| `disputes.decision_issued` | dispute, panel, outcome, reasoning |
| `reputation.event_recorded` | context, subject, source_events, classification |
| `sanctions.sanction_proposed` | subject, grounds, measure, limits |
| `sanctions.sanction_finalized` | sanction, decision, appeal_window |
| `appeals.appeal_submitted` | sanction, appellant, grounds |
| `appeals.appeal_decided` | appeal, independent_panel, outcome |
| `rehabilitation.step_completed` | plan, step, evidence |

## Solidarity and crisis

| Событие | Минимальный payload |
|---|---|
| `solidarity.campaign_opened` | fund, purpose, rules, period |
| `solidarity.contribution_verified` | campaign, kind, quantity, verifier |
| `solidarity.allocation_approved` | campaign, recipient_ref, rule, approvers |
| `solidarity.aid_delivered` | allocation, delivery proof, witness |
| `solidarity.campaign_closed` | campaign, totals, residue_rule |
| `reserves.threshold_crossed` | resource, level, threshold, snapshot |
| `crisis.mode_activated` | policy_version, mandate, area, period |
| `crisis.rationing_confirmed` | resource, rule_version, allocations_hash |
| `crisis.mode_closed` | activation, audit, remaining restrictions |

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
