# Трассировка требований

Статус: карта от ТЗ к проектированию и implementation slices.

| Раздел ТЗ | Основной документ | Slice | Основное доказательство |
|---|---|---|---|
| 0-3 Назначение/границы | `README`, `legal_model` | 0 | approved scope |
| 4 Устойчивость | `deployment`, `recovery_runbook` | 0, 11 | [backup/restore/update evidence](implemented_slice_11.md) |
| 5 Роли | `domain_model`, `gui_architecture` | 1 | authorization matrix |
| 6 Бизнес-процессы | `user_scenarios` | 3-7, 18 | E2E vertical flows and participant marketplace |
| 7 Кредит/паи/помощь/репутация | профильные policies | 6, 8, 9 | property + policy tests |
| 8 Резервы | `crisis_protocol` | 10 | [implemented Slice 10](implemented_slice_10.md) |
| 9 Офлайн | `offline_protocol`, ADR-0005/0006 | 11 | [implemented Slice 11](implemented_slice_11.md) |
| 10 Кризис | `crisis_protocol` | 10 | [implemented Slice 10](implemented_slice_10.md) |
| 11 Доверие/безопасность | `security`, `threat_model` | 1-12 | [node controls](implemented_slice_11.md), independent review |
| 12 Объектная модель | `domain_model`, `data_model` | 1-11 | migrations/constraints |
| 13 Архитектура | `architecture`, ADR | 0 | architecture tests |
| 14 API/UI | `api`, `gui_architecture`, `design_system` | все, 18 | OpenAPI/E2E/a11y and participant browser path |
| 15 NFR | deployment/observability/recovery | 0, 11, 12 | restore drill, capacity and RTO/RPO |
| 16 Тестирование | `testing_strategy` | все | release gates |
| 17 Приёмка | `production_readiness` | 12 | evidence pack |
| 18 Пилот | `pilot_runbook` | 12 | six-month report |
| 19 Этапы | `development_plan` | 0-14 | completed slices |
| 20 Репозиторий | `development_plan`, standards | 0 | scaffold layout |
| 21 Порядок кода | `development_plan` | 0-14 | backlog dependencies |
| 22 Документация/ИИ | `ai_development_rules` | все | PR checklist |
| 23 Бухгалтерия/право | `accounting_model`, `legal_model` | 5-12 | reconciliation/export |
| 24 Будущие модули | ADR до реализации | 13-14 и post-pilot | отдельный scope |
| 25 Open decisions | `open_decisions` | до feature | approved policies |
| 26 Готовность | `production_readiness` | 12 | signed readiness review |

## Группы критериев приёмки

| Критерии | Покрываются |
|---|---|
| 1-5 | Identity slice, RBAC tests |
| 6-16 | Inventory/rights, DB concurrency tests |
| 17-27 | Deals/clearing, property and E2E tests |
| 28-37 | Offline protocol and field drills |
| 38-46 | Crisis/reserves policies and drills |
| 47-57 | Security/audit/recovery evidence |
| 58-74 | Operations, GUI, accounting and pilot metrics |
| 75-87 | Share liability property tests and legal policy |
| 88-96 | Responsibility/custody/conflict-of-interest E2E |
| 97-106 | Solidarity tests, privacy and fund reconciliation |
| 107-120 | Reputation/sanctions/appeals/protocol tests |
| 121-131 | Offline distribution, crypto, outbox, restore gates |
| 132-145 | Admin console and operational clearing lifecycle |
| 146-155 | Node onboarding, trust limits and bounded node liability |
| 156-166 | Federated offers, logistics quotes and landed-cost search |
| 167-175 | Inter-node prepare/commit/apply clearing protocol |

## Правило PR

Feature PR указывает затронутые разделы ТЗ, документы, open decisions,
acceptance criteria и tests. Если строка карты меняется, обновляется этот файл
или создаётся ADR. Наличие API endpoint без соответствующего доказательства не
считается реализацией требования.
## Slice 12 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Защищённая наблюдаемость без PII labels | `api/operations.py`, `shared/core/metrics.py`, `modules/operations/application/status.py` | `test_metrics.py`, `test_operations_observability.py` |
| Локальная диагностика | `coopctl diagnostics` | deployed diagnostics и evidence pack |
| Read-only capacity runner | `tools/capacity.py`, `scripts/capacity-smoke.*` | unit tests и 500-request smoke |
| GUI эксплуатации | `OperationsView.tsx`, role-filtered workspace | API/component/type/build tests |
| Automated accessibility baseline | `frontend/src/test/accessibility.ts` | login/password DOM audit tests |
| Production evidence pack | `scripts/collect-production-evidence.*` | parser/syntax, runtime collection, checksum verification |
| Внешние решения | `docs/evidence_templates/` | только подписанные внешние отчёты; автоматической отметки нет |

## Slice 13 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Signed direct/indexed/offline search | discovery service, `CC-PEER-1`, offer index | unit/integration peer and search tests |
| Deterministic landed cost | decimal cost breakdown и `LANDED_COST_V1` | domain ranking tests и GUI formula |
| Trusted online transport | exact peer registry, certificate, capability, HTTPS/mTLS settings | protocol/fan-out tests |
| Goods/logistics saga | buyer receipts и home-node holds | API flow, oversell и expiry tests |
| Bilateral exposure | locked limits и `NodeExposure` reserve/commit/release | home-node integration test |
| Recovery | durable COMMITTING/CANCELLING и idempotent acknowledgements | backend flow и frontend retry tests |

## Slice 14 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Federated obligations and positions | revision `0018`, Decimal balances, exact versions | unit/API/integration tests |
| Signed snapshots and prepare | canonical artifacts, trust/exposure checks, expiry | protocol and database tests |
| Independent verification | deterministic proposal и local controller approval | coordinator/participant tests |
| Commit finality | all affected approvals и полный certificate package | certificate validation tests |
| Idempotent local apply | unique certificate apply, local transaction, receipt | duplicate/recovery tests |
| Loss of one node | durable pending apply и same-certificate recovery | three-node Docker acceptance |
| Operator workspace | typed client, role actions, evidence/finality tables | component/API/coverage gate |

## Slice 15 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Подписанный release manifest | `scripts/release_bundle.py`, Ed25519 и independent fingerprint | valid/wrong key tests и live verify |
| Полный offline bundle | четыре image archives и 22-file `node/` payload | clean volumes, `--pull never --no-build` |
| Fail-closed integrity | exact checksum inventory, no symlink/extra/path escape | altered/extra/traversal tests |
| Supply-chain inventory | per-image SBOM, license reports и pinned policy | independent reclassification и blocked-license test |
| Content identity | signed image IDs, layer digests и archive hashes | `--load-images` с post-load inspect |
| Linux/PowerShell parity | общий verifier в обоих `update-node` | shell syntax и PowerShell parser gates |
## Slice 16 trace

| Требование | Реализация | Проверка |
|---|---|---|
| FULL backup | DB, blobs, recovery material и verified release | manifest/checksum и independent restore |
| Exact runtime ACL | dump/restore GRANT и REVOKE без owner | `coop_app` init/API/worker after restore |
| Interrupted update | bounded faultpoints и durable previous-release state | after-migration automatic rollback |
| Successful update | signed target, FULL pre-backup, migration, readiness | isolated r1 -> r2 drill |
| Recovery release | verify/load и installed Compose match before destruction | r2 -> r1 destructive restore |
| Cross-platform operations | compatible UTF-8/path/process handling | Windows PowerShell live drill и shell syntax |
## Slice 17 trace

| Требование | Реализация | Проверка |
|---|---|---|
| API compatibility | scripts/openapi_compat.py и baseline 0.1.0 | 7 negative tests, 298 operations, exact mirror |
| Contract freshness | OpenAPI snapshot в backend test image | API snapshot equality test |
| Clearing properties | seeded local/federated graph matrix | 300 graphs и три input order |
| Previous-schema migration | scripts/test-migration.sh | 0017 -> 0018 -> 0017 -> 0018 |
| Repeatable concurrency | scripts/test-critical-quality.sh | 3 x 21 PostgreSQL critical tests |
| Journal after races | coopctl verify-journal | 556 events, sequence 556, no failures |
| Release ordering | critical-quality dependency | release job не запускается после failed gate |
## Slice 18 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Персональная главная | `api/participant.py`, `MemberHomeView.tsx` | component test и browser desktop/mobile |
| Паи и источник | account policy/contributions, доступный/protected/reserved расчёт | integration dashboard и demo source |
| История участника | own offers, purchases, sales, obligations, commitments | buyer/seller/farmer integration flow |
| Товар или услуга | participant publishing, units, evidence image | frontend tests и API integration |
| Рынок в сделку | revision `0020`, unique purchase-intent bridge, три obligations | idempotency/unit/PostgreSQL tests |
| Воспроизводимый учебный путь | account `farmer`, membership, role, share account, milk offer | repeated `seed-demo` and live login |
| Физическая передача сторонами | простые действия продавца и покупателя, quantity/condition/evidence | component tests и полный PostgreSQL flow |
| Приватность актов | party/carrier/private-admin scope для fulfillments и acceptances | unrelated-carrier integration assertion |
| Личная адресная книга и снимки точек | migration `0022`, owner-scoped participant API, profile/market selectors | CRUD/privacy/idempotency integration, API/component tests, desktop/mobile browser |