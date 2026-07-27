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
| 11 Доверие/безопасность | `security`, `threat_model` | 1-12, 19-20 | [node controls](implemented_slice_11.md), [local MFA and emergency access](implemented_slice_20.md), independent review |
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
## Slice 19 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Объяснимый сигнал, не обвинение | versioned rule, observed facts, thresholds, `automatic_decisions=0` | unit/integration и RU/EN component tests |
| Аномалии предложения и логистики | median price, republication burst, route unit-cost rules | deterministic rule tests и demo milk signal |
| Фиктивный спрос | cancelled/compensated/expired intent burst | PostgreSQL rule scan |
| Риски поручительства и обеспечения | reciprocal guarantee и collateral concentration | domain query и enforcement integration |
| Ограничение автоматической операции | общий fail-closed enforcement в federation/risk commands | blocked publish и release after review integration |
| Независимая ручная проверка | `RISK_ADMIN` detector, отдельный `AUDITOR`, READY evidence | same-member rejection и evidence decision test |
| Неизменяемость основания | revision `0023`, immutable facts/thresholds trigger, signed events | direct SQL update rejection |
| Нет обхода через global node role | revision `0024`, обязательный cooperative owner в offer/quote/intent и fail-closed context resolution | populated migration invariants и global-role HOLD integration |
| Операторское рабочее место | overview, filters, fact/threshold explanation, decision form | API/component/build и browser acceptance |
| Первый контур раздела 24.5 | шесть правил версии 1.0.0 и явно зафиксированный gap | [границы Slice 19](implemented_slice_19.md#проверка-и-границы) |
## Slice 20 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Локальный второй фактор | encrypted TOTP factor, replay/brute-force controls, QR enrollment | AES-GCM/TOTP unit tests, API and component flow |
| Короткое подтверждение личности | server-side session step-up with expiry | critical endpoint integration assertions |
| Восстановление без внешних сервисов | two-person recovery, Argon2id temporary password, session/factor revoke | end-to-end PostgreSQL flow and signed events |
| Ограниченный аварийный доступ | allowlist, scope, 15-60 minute expiry, independent approval and usage audit | activation/use/revoke integration flow |
| Запрет саморасширения | permanent assignment source required for ordinary role lifecycle | break-glass delegation rejection test |
| Проверяемая власть | linked authority assignment and signed lifecycle events | migration FK/backfill cycle and signature assertions |
| Понятный интерфейс | Security workspace, RU/EN errors and reason labels | component, locale, typecheck, build and browser checks |

## Slice 21 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Все классы раздела 24.5 | 13 requirement keys и 15 versioned rules алгоритма 2.0.0 | enum/manifest completeness unit test |
| Связанные аккаунты | active related links, reciprocal positive events, transitive resource components | positive/negative rule tests |
| Дробление операций | четыре малых обязательства, 80% aggregate, предупреждение вместо недостижимого over-limit состояния | threshold unit test |
| Репутация и пожертвования | contextual burst и совпадение verifier с автором последующей оценки | temporal/actor negative tests |
| Связанные решения и помощь | related allocation/approval/arbitration, overlapping campaigns | conflict and interval tests |
| Обход санкции | related member создан после начала active sanction | positive/old-account tests |
| Воспроизводимость запуска | migration 0028, algorithm, manifest SHA-256 и dataset version в scan | PostgreSQL integration и migration cycle |
| Честный статус калибровки | synthetic scope, пустой pilot FPR, `production_approved=false` | catalog API/component test |
| Понятный операторский экран | 13/15, локализованные риски и реакции без внутренних table codes | RU/EN locale, component, build и browser smoke |
| Реальный пилотный FPR | не заявлен и остаётся внешним gate | unchecked production-readiness item |

## Slice 22 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Раздельные `Member`, `Cooperative`, `Membership`, `User` | пять вкладок реестра и отдельные create/list/transition contracts | frontend component и PostgreSQL integration flow |
| Отдельный `Node` | локальный и внешний node registry с переходом в trust workspace | component test и federation API contract |
| Минимальные полномочия и scope | server-side cooperative filtering и command authorization | scoped registrar integration tests |
| Сохранение истории | доменные status transitions, optimistic version, без DELETE | transition policy tests и signed audit assertions |
| Немедленное отключение входа | `User -> DISABLED` отзывает sessions в одной транзакции | issued-session revocation integration test |
| Обратная совместимость API | single-scope inference только при одном доступном кооперативе | OpenAPI compatibility и legacy request test |

## Slice 23 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Ручной duplicate review | exact identifier block, normalized-name candidates и explicit distinct decision | unit/API/component tests |
| Безопасный массовый ввод | staging, bounded CSV, dry run, row report, independent `DATA_STEWARD` decision | parser and PostgreSQL integration tests |
| Атомарное применение | advisory lock, repeat duplicate check и stale-preview rejection | concurrency/state-change integration tests |
| Минимизация PII | no source CSV/plain identifiers, only hashes and safe row projection | database assertions |
| Отсутствие скрытых полномочий | import creates no User, Membership, Role, shares or limits | end-to-end assertions |
| Понятный RU/EN GUI | template, preview, result/reason labels and safe errors | locale/component/browser checks |

## Slice 24 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Отдельная machine identity | `service_clients`, credentials/tokens и отдельная auth dependency | service token rejected by human endpoint integration test |
| Один ответственный owner | mandatory cooperative/contact and owner-scoped reads/commands | scoped lifecycle integration and GUI tests |
| Least privilege | exact two-scope allowlist, per-endpoint `require_scope`, no direct peer fanout | normalization, denial and OpenAPI tests |
| Network/rate/expiry bounds | normalized CIDR without `/0`, source-bound token, DB minute bucket, max one year | unit and PostgreSQL token flow |
| Независимое подключение | permanent manager request, different permanent security reviewer and TOTP step-up | explicit self-review `409` and successful independent decision |
| Secret safety | high-entropy one-time response, hash-only storage, no replay secret | integration database and response assertions |
| Немедленная защита | suspend/revoke credentials/tokens without changing human sessions | revocation end-to-end assertion |
| Bounded runtime state | worker expiry plus 30-day token and 2-day rate-bucket retention | cleanup integration assertion |
| Операторский интерфейс | separate Integrations tab, request queue, TOTP dialog, one-time credential dialog | 170-test frontend suite, RU/EN component tests |
| Воспроизводимый demo | active catalog bridge and registrar rotation request without plaintext secret | repeated seed-demo PostgreSQL test |
| Миграция и контракт | revision `0031`, generated backend/frontend OpenAPI and typed API client | downgrade/upgrade/check and compatibility gate |
| Deployment trust boundary | gateway-only ingress and trusted forwarded source requirement | Compose inspection plus external security review remains open |
## Slice 25 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Нет silent merge/delete | source status `MERGED`, self-FK survivor mapping, no DELETE | PostgreSQL integration asserts source and mapping |
| Сквозная персональная ответственность | permanent requester, independent personal security reviewer, TOTP step-up | self-review `409` and successful second-user decision |
| Сохранение подписанной истории | external FK scan blocks journal/domain references; events append-only | dynamic blocker function and signed event assertions |
| Безопасный перенос identity | identifiers, membership, addresses and at most one login only | clean transfer integration flow |
| Fail-closed для паёв и сделок | runtime pg_catalog FK discovery for current and future tables | external-reference blocker assertion |
| Optimistic/idempotent lifecycle | both member versions, case version, expiry, idempotency records | stale/replay integration assertions |
| Понятный RU/EN интерфейс | source/survivor wording, grouped blockers, protected decision dialog | locale symmetry and component tests |
| Воспроизводимый demo | clean duplicate and pending independent case | repeat seed integration test |
| Миграция и контракт | revision `0032`, OpenAPI and typed frontend schema | upgrade/downgrade/check and snapshot gates |
| Юридические transfer/succession rules | не заявлены как закрытые | OD-038 и production-readiness external gate |

## Slice 26 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Немедленно остановить действия | User `DISABLED`, session revoke, Membership `SUSPENDED` в одной транзакции | PostgreSQL lifecycle integration |
| Сквозная персональная ответственность | permanent requester, другой permanent personal `SECURITY_ADMIN`, TOTP | self-review/temporary-role denial и successful decision |
| Не потерять хозяйственные связи | grouped FK summary без UPDATE/DELETE экономических записей | reference-map unit и database assertions |
| Fail-closed восстановление | versioned snapshot и повторная блокировка Member/User/Membership | missing/version/status blocker tests |
| Безопасное отклонение | точное восстановление snapshot, старые sessions не возвращаются | reject integration assertions |
| Optimistic/idempotent lifecycle | case/row versions и сохранённый response по `Idempotency-Key` | stale и replay tests |
| Понятный RU/EN интерфейс | хозяйственные группы, последствия, blockers и TOTP dialog | locale symmetry, component и browser checks |
| Воспроизводимый demo | `Svetlana Morozova`, suspended membership, pending exit case | repeated seed и live Docker inspection |
| Миграция и контракт | revision `0033`, OpenAPI и typed frontend schema | downgrade/upgrade/check и snapshot gates |
| Юридическое наследование/расчёты | намеренно не заявлены как закрытые | ADR-0013, OD-038 и external legal gate |
