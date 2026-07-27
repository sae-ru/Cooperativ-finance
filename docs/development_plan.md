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

Статус: runtime и CI реализованы; подписанный offline bundle, backup/restore automation и production TLS остаются обязательными до завершения slice.

- Python/FastAPI/SQLAlchemy/Alembic skeleton;
- React/TypeScript PWA skeleton;
- PostgreSQL, Nginx, Compose;
- config/secrets conventions;
- request id, error envelope, health;
- CI, formatting, typing, tests, SBOM baseline;
- offline build/install smoke.

Готово: новый host запускает skeleton из offline bundle.

## Slice 1. Identity and audit

Статус: реализован; доказательства и эксплуатационные ограничения приведены в [implemented_slice_1.md](implemented_slice_1.md).

- User, Member, Cooperative, Membership, RoleAssignment;
- local login, refresh sessions, revoke, step-up interface;
- RBAC scope и separation of duties;
- immutable audit, idempotency registry;
- рабочее место «Сегодня», admin console участников/организаций и active role.

Готово: назначение/отзыв роли отражается во всех новых командах и audit.

## Slice 2. Signed journal and responsibility

Статус: реализован; доказательства и ограничения приведены в [implemented_slice_2.md](implemented_slice_2.md).

- event envelope, canonicalization adapter, signing port;
- node sequence/hash chain;
- transactional outbox/worker;
- ResponsibilityAssignment и Approval flow;
- GUI responsibility chain и canonical preview.

Готово: одна критическая тестовая команда атомарно создаёт state/event/audit/outbox.

## Slice 3. Inventory vertical flow

Статус: реализован; доказательства и ограничения приведены в [implemented_slice_3.md](implemented_slice_3.md).

- Product, Unit, Warehouse, InventoryLot;
- independent attestation, quality, evidence blobs;
- custody transfer;
- mobile receive/inspect/transfer screens;
- stock constraints and discrepancy case.

Готово: реальная партия проходит кладовщика и контролёра с печатным актом.

## Slice 4. Commodity rights

Статус: реализован; доказательства и ограничения приведены в [implemented_slice_4.md](implemented_slice_4.md).

- reservation и lot balance;
- issue, transfer, freeze, redeem;
- concurrency/idempotency protection;
- rights/availability GUI;
- proof from lot to recipient.

Готово: право нельзя выпустить сверх партии или погасить дважды.

## Slice 5. Deals and obligations

Статус: реализован; доказательства и ограничения приведены в [implemented_slice_5.md](implemented_slice_5.md).

- versioned terms и party confirmations;
- obligations, partial fulfillment, acceptance, dispute;
- due/overdue workflow;
- human-readable obligation UI;
- logistics order integration.

Готово: сделка имеет доказуемое частичное исполнение и остаток.

## Slice 6. Shares and bounded risk

Статус: реализован; доказательства, ограничения и проверки приведены в [implemented_slice_6.md](implemented_slice_6.md).

- share contours, protected amount, reservations;
- credit limits, guarantees, role bonds;
- aggregate related-party exposure;
- exposure preview GUI;
- liability case без automatic execution.

Готово: любое рискованное действие показывает и соблюдает max loss.

## Slice 7. Bilateral clearing

Статус: реализован; доказательства, ограничения и проверки приведены в [implemented_slice_7.md](implemented_slice_7.md).

- pure deterministic engine;
- input snapshot, preview, dispute window, finalize;
- clearing proof/verifier;
- полный операционный cycle: collect/freeze/preview/review/dispute/finalize/reconcile;
- operator GUI, proof verifier и participant statement;
- accounting export draft.

Готово: один weekly cycle воспроизводится из proof.

## Slice 8. Disputes and trust

Статус: реализован; доказательства, ограничения и проверки приведены в [implemented_slice_8.md](implemented_slice_8.md).

- disputes, conflicts of interest, decisions;
- sanctions/protective measures;
- independent appeals и rehabilitation;
- reputation events/context projections;
- auditor/arbitrator workspaces.

Готово: ошибочная мера проходит appeal и корректно перестраивает profile.

## Slice 9. Solidarity

Статус: реализован; доказательства, ограничения и проверки приведены в [implemented_slice_9.md](implemented_slice_9.md).

- fund, campaign, pledge, contribution;
- allocation dual control, delivery proof, complaint;
- privacy scopes и aggregated report;
- explicit tests no debt/no reputation benefit.

Готово: одна campaign проходит от вклада до подтверждённой выдачи и reconciliation.

## Slice 10. Reserves and crisis

Статус: реализован; доказательства, ограничения и проверки приведены в [implemented_slice_10.md](implemented_slice_10.md).

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
без удаления истории. Реализация и доказательства: [implemented_slice_11.md](implemented_slice_11.md).

## Slice 12. Pilot hardening

Инженерный baseline реализован: protected operational snapshot/metrics,
operator GUI, capacity runner, automated DOM accessibility checks и PII-free
production evidence pack. Независимые security/legal review, target-host
capacity, ручная accessibility matrix и фактический шестимесячный пилот остаются
обязательными внешними критериями. Доказательства: [implemented_slice_12.md](implemented_slice_12.md).

- load/capacity on target hardware;
- complete observability;
- accessibility/browser matrix;
- external security and legal review;
- six-month pilot operations and corrective releases;
- production readiness evidence pack.

Федерация нескольких организаций начинается после slice 12 и отдельного ADR.

## Slice 13. Federated discovery and logistics

Статус: реализован и проверен; evidence: [implemented_slice_13.md](implemented_slice_13.md).

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

Статус: реализован и проверен на стенде из трёх независимых узлов; доказательства: [implemented_slice_14.md](implemented_slice_14.md).

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

## Slice 15. Signed offline release

Статус: реализован и проверен чистой установкой без pull/build; доказательства:
[implemented_slice_15.md](implemented_slice_15.md).

- Ed25519 release manifest и независимый public-key fingerprint;
- полный checksum inventory и запрет unsigned filesystem entries;
- четыре runtime image archive с immutable content ID;
- CycloneDX SBOM, license reports и pinned license policy;
- минимальный node installation payload;
- общий fail-closed verifier для Linux и PowerShell update;
- CI contract и негативные tamper tests.

Готово: новый узел устанавливается из подписанного bundle без registry и
исходного кода. Production key ceremony, ручной license review и внешний
readiness review остаются отдельными обязательными решениями.

## Slice 16. FULL recovery and update rollback

Статус: реализован и проверен на отдельном update/restore стенде; доказательства:
[implemented_slice_16.md](implemented_slice_16.md).

- FULL backup требует encrypted recovery material и verified signed release;
- PostgreSQL dump сохраняет точные runtime ACL;
- update имеет bounded readiness и управляемые faultpoints;
- ошибка после migration автоматически возвращает previous release;
- destructive restore загружает точный release, DB, ACL и blobs;
- init/bootstrap/health/journal обязательны после recovery.

Готово: interrupted update, application rollback, independent backup restore и
полный возврат r2 -> r1 проверены. Физический резервный host, production keys,
реальные custodians и migration разных schemas остаются внешними gates.

## Slice 17. Critical quality gates

Статус: реализован и проверен; доказательства:
[implemented_slice_17.md](implemented_slice_17.md).

- fail-closed OpenAPI compatibility и exact frontend mirror;
- committed contract соответствует фактическому приложению;
- 300 deterministic property graphs для local/federated clearing;
- isolated 0017 -> 0018 -> 0017 -> 0018 migration drill;
- три последовательных PostgreSQL concurrency rounds;
- итоговая проверка подписанного журнала;
- release job зависит от critical-quality gate.

Готово: локально воспроизводимый gate создает checksum evidence pack и не
допускает release при несовместимом API, потере данных миграцией, flaky race
или нарушенной journal sequence. Remote CI на конкретном commit и migration с
фактического предыдущего production release остаются внешними evidence.

## Slice 18. Кабинет пайщика и сквозной обмен

Статус: реализован и проверен; доказательства:
[implemented_slice_18.md](implemented_slice_18.md).

- персональный профиль, паи, источник оценки и история участника;
- публикация товара или услуги с изображением;
- приватная адресная книга и неизменяемые снимки точек сделки;
- поиск, заказ, локальная сделка, доставка, передача и приёмка;
- понятный учебный профиль обычного пайщика.

## Slice 19. Объяснимая проверка аномалий

Статус: реализован первый сквозной контур; доказательства:
[implemented_slice_19.md](implemented_slice_19.md).

- шесть детерминированных правил версии 1.0.0;
- неизменяемые факты и пороги сигнала;
- реальное удержание опасных автоматических команд;
- независимый аудитор, READY evidence и запрет самоаудита;
- scoped API, русско-английское рабочее место и демосигнал;
- честно зафиксированный остаток правил раздела 24.5 ТЗ.
## Slice 20. Локальная MFA и аварийный доступ

Статус: реализован TOTP-контур; доказательства и честные остаточные ограничения:
[implemented_slice_20.md](implemented_slice_20.md).

- AES-256-GCM encryption отдельного TOTP seed и безопасная ротация;
- server-side step-up с expiry и replay/brute-force controls;
- двухэтапное восстановление с отзывом сессий и факторов;
- scoped/time-bounded break-glass без превращения в постоянную роль;
- подписанные recovery/break-glass events и audit каждого использования;
- русско-английский кабинет безопасности с QR enrollment;
- миграционный rollback/forward и сквозные backend/frontend tests.

Готово: потеря второго фактора восстанавливается двумя независимыми людьми, а
временное аварийное право исчезает из уже открытой сессии после отзыва. WebAuthn,
независимый security review и реальное учение остаются production gates.
## Slice 21. Полный версионированный антифрод-контур

Статус: реализован инженерный контур всех классов раздела 24.5; доказательства и
честные остаточные ограничения: [implemented_slice_21.md](implemented_slice_21.md).

- алгоритм `2.0.0`, 13 классов риска и 15 объяснимых правил;
- SHA-256 манифеста и версия синтетического regression-набора в каждом scan;
- связанные аккаунты, дробление лимитов, критические ресурсы, репутация,
  солидарная помощь, конфликт решений и обход санкций;
- fail-closed `HOLD` для участника без автоматического обвинения или санкции;
- scoped rule-catalog API и понятное RU/EN рабочее место;
- парные положительные/отрицательные проверки новых правил;
- миграция `0028`, OpenAPI и generated frontend client.

Готово: каждый класс раздела 24.5 имеет versioned rule и синтетический
регрессионный сценарий. Реальный размеченный пилотный набор, утверждённая частота
ложных срабатываний, drift/privacy/legal review остаются обязательными внешними
production gates; `production_approved=false` до их закрытия.

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

## Slice 22. Раздельный административный реестр

Статус: реализован базовый lifecycle; доказательства и остаточные ограничения:
[implemented_slice_22.md](implemented_slice_22.md).

- отдельные рабочие реестры Cooperative, Member, Membership, User и Node;
- server-side cooperative scope для списков, overview и команд;
- versioned status transitions без удаления истории;
- немедленный отзыв сессий при отключении User и запрет self-disable;
- migration `0029`, совместимый OpenAPI, integration и component tests.

Готово: администратор не смешивает хозяйственного участника, его организационную
связь, login и технический узел. Duplicate merge, service clients и массовый
staging-import остаются отдельными последующими срезами.
## Slice 23. Безопасный ввод участников

Реализован production-срез ручной duplicate review и массового staging-import:

- ручная регистрация проверяет точный identifier и нормализованное имя, не выполняет silent merge;
- CSV проходит staging, dry run, построчный отчёт, независимое решение `DATA_STEWARD` и применение `MEMBER_REGISTRAR`;
- применение повторно проверяет дубликаты под cooperative advisory lock и атомарно останавливает устаревший preview;
- исходные identifiers не сохраняются в открытом виде; импорт не создаёт роли, логины, членства, паи или лимиты;
- migration `0030`, совместимый OpenAPI, backend integration и frontend component tests.

Service-client lifecycle реализован в Slice 24. Управляемое объединение
подтверждённых дубликатов остаётся отдельным следующим срезом, потому что
требует собственной evidence/decision/id-map процедуры.

## Slice 24. Внешние программные интеграции

Реализован production-контур service clients:

- machine identity полностью отделена от `User`, `Member` и `Node`;
- owner cooperative, named technical contact, exact scopes, CIDR allowlist, rate limit, expiry и version обязательны;
- create/update/rotate/reactivate проходят permanent manager request и независимое permanent security decision с step-up;
- секрет выдаётся один раз, хранится только как hash и не возвращается при idempotency replay;
- machine token короткоживущий, server-side revocable, source-bound и не принимается human endpoints;
- suspend/revoke немедленно отзывают machine tokens, не останавливая human sessions;
- runtime tables имеют worker retention, migration `0031`, совместимый OpenAPI, backend/frontend tests, RU/EN GUI и demo rotation request.

Доказательства и остаточные ограничения: [implemented_slice_24.md](implemented_slice_24.md).
Управляемое объединение подтверждённых дубликатов реализовано в Slice 25.

## Slice 25. Безопасное объединение дубликатов участников

Реализован консервативный merge lifecycle:

- source никогда не удаляется и после решения хранит `MERGED -> survivor` mapping;
- registrar/data steward создаёт versioned evidence case, другой permanent security administrator решает его с TOTP step-up;
- PostgreSQL динамически находит все внешние ссылки на source и fail-closed блокирует паи, обязательства, сделки, доставку, signed history и будущие доменные таблицы;
- автоматически переносятся только identity identifiers, membership, participant addresses и единственный login без конфликтов;
- blocker summary локализуется в GUI без показа SQL constants;
- expiration, idempotency, signed events, audit, demo case, migration `0032` и backend/frontend tests входят в срез.

Доказательства и границы: [implemented_slice_25.md](implemented_slice_25.md).
Следующий production-срез должен реализовать доменные transfer/succession процедуры для экономически используемой identity; межкооперативное и наследственное правопреемство остаётся юридически открытым.

## Slice 26. Контролируемый выход и преемственность

Реализована code-level процедура немедленного containment при добровольном выходе, смерти или недееспособности участника. Создание дела отключает логины, отзывает сеансы и приостанавливает членства; независимый постоянный `SECURITY_ADMIN` с TOTP подтверждает либо отклоняет обстоятельство после повторной проверки версий.

Экономические связи не переносятся и не закрываются: паи, ответственность, долги, товарные права, сделки, логистика, споры, репутация и подписанная история остаются на исходной карточке до отдельных типизированных решений. Юридическая succession policy остаётся открытой в OD-038.

Доказательства и границы: [implemented_slice_26.md](implemented_slice_26.md), [ADR-0013](decisions/ADR-0013-member-continuity-containment.md).

## Slice 27. Аварийная непрерывность физического хранения

После подтвержденной смерти или недееспособности система удерживает партии прежнего складского назначения, требует независимый пересчет с доказательствами, отдельное TOTP-одобрение и личную приемку временным хранителем. До приемки прежний `custodian_assignment_id` не меняется. Расхождение переводит дело в `BLOCKED` без автоматического исправления количества.

Доказательства и границы: [implemented_slice_27.md](implemented_slice_27.md), [ADR-0014](decisions/ADR-0014-emergency-custody-continuity.md). Имущественное наследование остается вне Slice 27.

## Slice 28. Fail-closed production deployment

Реализован единый канонический environment contract и защищённый путь запуска:

- `production`, а не неиспользуемый alias, во всех runtime/update/evidence checks;
- сохранение режима, release и проверенных operational artifacts в `.env`;
- запрет in-place demo -> production и hardened -> demo;
- независимый PostgreSQL guard по `demo_data_loaded` и environment transition;
- обязательные signed bundle, public key, expected release и pinned license policy;
- загрузка проверенных image IDs и только `--no-build --pull never`;
- запрет dirty-evidence override и production faultpoints/data-only backup;
- одинаковый контракт Windows/Linux и regression tests.

Доказательства и внешние границы: [implemented_slice_28.md](implemented_slice_28.md). Этот срез устраняет ложный production mode, но не заменяет подписанный readiness review, production keys, внешний security/legal review и полевой pilot.

## Slice 29. Локальная готовность узла и безопасная диагностика

Реализован автономный эксплуатационный контур:

- фоновая host-проба диска, часов и ИБП с идемпотентным запуском и
  подтверждённой остановкой на Windows/Linux;
- регистрация свежести и полноты последней резервной копии;
- серверная оценка диска, часов БД, backup, сертификатов и ИБП;
- защищённые API, bounded Prometheus metrics и RU/EN GUI без сырых кодов;
- зашифрованный диагностический пакет с точным inventory, автономной проверкой
  и персональной audit-записью скачивания;
- OpenAPI, unit/integration/frontend/script tests и signed release payload.

Доказательства и границы: [implemented_slice_29.md](implemented_slice_29.md).
Следующий code-level приоритет определяется по оставшимся пунктам матрицы ТЗ;
целевой host, внешняя security/legal проверка, RTO/RPO и полевой пилот остаются
отдельными обязательными production gates.

## Slice 30. Финальная ограниченная компенсация

Реализован контролируемый путь возмещения из личного гарантийного резерва:

- assessment и жалоба не двигают паи автоматически;
- перенос требует финального trust decision, точной связности дел и счетов и
  исключает всех принимавших решения по делу из роли авторизующего оператора;
- сумма ограничена assessed/established loss, `max_loss`, остатком commitment и
  доступной незащищённой частью личного `GUARANTEE` счёта;
- авторизация только резервирует сумму, а атомарный дебет/кредит происходит после
  личного принятия получателем;
- participant home, операторская вкладка, RU/EN, детерминированный demo seed,
  migration `0035`, OpenAPI и backend/frontend/browser tests входят в срез.

Доказательства и остаточные юридические границы:
[implemented_slice_30.md](implemented_slice_30.md). Внешняя правовая и security
проверка, backup/restore на целевом host и полевой pilot остаются обязательными
production gates.
