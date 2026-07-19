# План разработки

Статус: исполнимая последовательность production implementation.

## Подход

Разработка идёт вертикальными slices. Каждый slice включает domain, migration,
API, GUI, audit, events, tests, docs и эксплуатацию. Интерфейс не откладывается
до конца проекта.

## Репозиторий

```text
backend/
frontend/
infra/
docs/
scripts/
tests/
```

Backend и frontend имеют независимые lock-файлы. OpenAPI является контрактом,
из которого frontend получает типизированный client; ручное расхождение DTO
запрещено.

## Slice 0. Engineering foundation

- Python/FastAPI/SQLAlchemy/Alembic skeleton;
- React/TypeScript PWA skeleton;
- PostgreSQL, Nginx, Compose;
- config/secrets conventions;
- request id, error envelope, health;
- CI, formatting, typing, tests, SBOM baseline;
- offline build/install smoke.

Готово: новый host запускает skeleton из offline bundle.

## Slice 1. Identity and audit

- User, Member, Cooperative, Membership, RoleAssignment;
- local login, refresh sessions, revoke, step-up interface;
- RBAC scope и separation of duties;
- immutable audit, idempotency registry;
- рабочее место «Сегодня», admin console участников/организаций и active role.

Готово: назначение/отзыв роли отражается во всех новых командах и audit.

## Slice 2. Signed journal and responsibility

- event envelope, canonicalization adapter, signing port;
- node sequence/hash chain;
- transactional outbox/worker;
- ResponsibilityAssignment и Approval flow;
- GUI responsibility chain и canonical preview.

Готово: одна критическая тестовая команда атомарно создаёт state/event/audit/outbox.

## Slice 3. Inventory vertical flow

- Product, Unit, Warehouse, InventoryLot;
- independent attestation, quality, evidence blobs;
- custody transfer;
- mobile receive/inspect/transfer screens;
- stock constraints and discrepancy case.

Готово: реальная партия проходит кладовщика и контролёра с печатным актом.

## Slice 4. Commodity rights

- reservation и lot balance;
- issue, transfer, freeze, redeem;
- concurrency/idempotency protection;
- rights/availability GUI;
- proof from lot to recipient.

Готово: право нельзя выпустить сверх партии или погасить дважды.

## Slice 5. Deals and obligations

- versioned terms и party confirmations;
- obligations, partial fulfillment, acceptance, dispute;
- due/overdue workflow;
- human-readable obligation UI;
- logistics order integration.

Готово: сделка имеет доказуемое частичное исполнение и остаток.

## Slice 6. Shares and bounded risk

- share contours, protected amount, reservations;
- credit limits, guarantees, role bonds;
- aggregate related-party exposure;
- exposure preview GUI;
- liability case без automatic execution.

Готово: любое рискованное действие показывает и соблюдает max loss.

## Slice 7. Bilateral clearing

- pure deterministic engine;
- input snapshot, preview, dispute window, finalize;
- clearing proof/verifier;
- полный операционный cycle: collect/freeze/preview/review/dispute/finalize/reconcile;
- operator GUI, proof verifier и participant statement;
- accounting export draft.

Готово: один weekly cycle воспроизводится из proof.

## Slice 8. Disputes and trust

- disputes, conflicts of interest, decisions;
- sanctions/protective measures;
- independent appeals и rehabilitation;
- reputation events/context projections;
- auditor/arbitrator workspaces.

Готово: ошибочная мера проходит appeal и корректно перестраивает profile.

## Slice 9. Solidarity

- fund, campaign, pledge, contribution;
- allocation dual control, delivery proof, complaint;
- privacy scopes и aggregated report;
- explicit tests no debt/no reputation benefit.

Готово: одна campaign проходит от вклада до подтверждённой выдачи и reconciliation.

## Slice 10. Reserves and crisis

- reserve targets/snapshots/thresholds;
- signed crisis activation/review/close;
- rationing preview/confirm;
- crisis GUI и paper forms;
- expiry and anti-abuse controls.

Готово: полевое учение активирует и закрывает ограниченный режим.

## Slice 11. Offline node resilience

- offline epochs;
- trust onboarding внешнего узла, technical challenge и trust contract;
- owner, именованные ответственные, bilateral limits, node bond и exposure;
- signed package export/import;
- inbox, simulation, conflict cases;
- paper form ingestion;
- backup/restore/update/rollback automation;
- security incident, key rotate/revoke/quarantine.

Готово: два разделённых узла создают контролируемый конфликт и разрешают его
без удаления истории.

## Slice 12. Pilot hardening

- load/capacity on target hardware;
- complete observability;
- accessibility/browser matrix;
- external security and legal review;
- six-month pilot operations and corrective releases;
- production readiness evidence pack.

Федерация нескольких организаций начинается после slice 12 и отдельного ADR.

## Slice 13. Federated discovery and logistics

- signed offer index и direct/indexed/cached search;
- product/unit/quality federation mapping;
- logistics quote protocol и route components;
- landed cost breakdown и deterministic ranking;
- offer/quote freshness, privacy и antifraud;
- purchase intent и goods/logistics reservation saga;
- GUI поиска, сравнения и подготовки сделки.

Готово: локальный узел находит предложения нескольких узлов, показывает
воспроизводимую landed cost и завершает либо компенсирует reservation saga.

## Slice 14. Inter-node clearing

- federated obligation references и node positions;
- signed snapshots и prepare receipts;
- deterministic proposal и independent node verification;
- approvals всех affected home nodes;
- commit certificate и idempotent local apply;
- pending-apply recovery и apply receipts;
- federated proof, selective disclosure и reconciliation;
- regional/local clearing workspaces.

Готово: три узла завершают цикл без общей БД; потеря coordinator после commit
не нарушает финальность, а lagging node применяет certificate после recovery.
## Definition of Done для каждого slice

- approved policy/open decision для затронутого поведения;
- domain invariants and state diagrams;
- migration and rollback/recovery;
- API/OpenAPI and frontend typed client;
- role-specific GUI including error/offline/print state;
- event schemas, audit и idempotency;
- unit/property/integration/E2E/security tests;
- threat model delta;
- observability and runbook;
- no unresolved critical/high defects;
- demo на production-like offline node.

## Первые 90 дней

Дни 1-30: slices 0-1, ADR, CI, offline skeleton.

Дни 31-60: signed journal, outbox, responsibility и первая партия.

Дни 61-90: independent attestation, custody, evidence, rights reservation и
restore drill. Цель: один реальный товарный процесс, а не широкий mockup.

## Команда

Минимум для production темпа: product/domain lead, architect/backend lead,
2-3 backend, 2 frontend, QA automation, UX/product designer, DevOps/security и
постоянные part-time legal/accounting/audit owners. Один разработчик может
создать кодовую основу, но не независимо утвердить хозяйственные правила и
полевую безопасность.
