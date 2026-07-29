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
| Защищённая наблюдаемость без PII labels | `api/operations.py`, `shared/core/metrics.py`, `modules/operations/application/status.py`, `readiness.py` | `test_metrics.py`, `test_host_readiness.py`, `test_operations_observability.py` |
| Host readiness без Интернета | `scripts/operational_status.py`, `.operations` read-only mount, диск/часы/backup/certificate/UPS checks | 41 script tests, backend unit/integration, Windows start/stop smoke; target Linux/UPS evidence открыт |
| Локальная диагностика | encrypted API/GUI, `diagnostics.py`, `scripts/diagnostic_bundle.py`, append-only export audit | crypto/tamper/duplicate/oversize tests и PostgreSQL audit integration; independent privacy review открыт |
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
| API compatibility | scripts/openapi_compat.py и baseline 0.1.0 | 7 negative tests, 363 operations, exact mirror |
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

## Slice 27 trace

| Требование | Реализация и доказательство |
|---|---|
| Заблокировать ключи умершего/недееспособного | подтвержденный Slice 26 `DEATH_OR_INCAPACITY`; только contained source |
| Провести инвентаризацию | per-lot item, независимый контролер, обязательное evidence |
| Два независимых подтверждения | отдельный `SECURITY_ADMIN` с TOTP и личная приемка кандидатом |
| Прежний ответственный до приемки | `custodian_assignment_id` не меняется до candidate acceptance; integration test |
| Не допустить анонимный комитет | requester, counter, approver и accepter записаны персонально в case, event и audit |
| Остановить операции на время | `continuity_hold_case_id` блокирует контроль, расхождение, передачу и погашение права |
| Не наследовать роль и репутацию | создается новое временное назначение; исходная роль, паи и репутация не переносятся |
| Юридическая собственность и наследники | намеренно не заявлены закрытыми; OD-019, OD-033, OD-038 |

## Slice 28 trace

| Требование | Реализация и доказательство |
|---|---|
| Один production environment contract | canonical resolver принимает только пять значений; `prod` отклоняется |
| Не выдавать dev за production | `start.*` записывает `COOP_ENVIRONMENT=production` в `.env`; status хранит то же значение |
| Не занести demo в production | config/known-credential preflight и PostgreSQL `demo_data_loaded` guard |
| Только подписанный выпуск | bundle signature, expected release, independent public key и pinned policy обязательны |
| Не собирать и не загружать иные образы | verifier сверяет content ID; Compose использует `--no-build --pull never` |
| Не потерять recovery context после закрытия shell | абсолютные bundle/key paths и policy hash атомарно сохраняются; backup/update читают их общим helper |
| Не обойти update/evidence ограничения | общий resolver, запрет faultpoint/build/DATA_ONLY и dirty override в production |
| Windows/Linux parity | PowerShell/Linux wrappers, parser checks и shared regression suite |
| Внешнее разрешение production | намеренно не заявлено закрытым; подписанный readiness review остаётся обязательным |

## Slice 30 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Жалоба не списывает паи | assessment остаётся `NOT_EXECUTED`; отдельная compensation command | integration test до финальности и signed event assertions |
| Только финальное решение | точная liability/trust linkage; appeal должна завершиться `AFFIRMED` | original/appeal lifecycle integration test |
| Независимая авторизация | controller исключается, если был стороной, assessor или участником любого решения по делу | original и appeal arbitrator denial tests |
| Ограниченная личная ответственность | минимум из assessed loss, established loss, `max_loss`, commitment remainder и незащищённого остатка | service/DB boundary tests |
| Protected shares не затрагиваются | source только `GUARANTEE`, invariant `balance - protected - held` | PostgreSQL balance assertions до/после |
| Согласие до расчёта | authorize увеличивает только `executed_not_settled`; recipient принимает лично | participant acceptance test и browser path |
| Атомарный settlement | source/destination/commitment/liability/transfer/event меняются одной транзакцией | exact before/after balance assertions |
| Scoped visibility | участник видит только перенос, где он responsible/recipient; служебные роли ограничены cooperative | API и frontend tests |
| Повторяемые демоданные | production services, стабильные ids/idempotency keys, сохранение pending/settled state | repeated seed integration test |
| Миграционная и API совместимость | revision `0035`, fail-closed downgrade с историей, 367-operation exact OpenAPI mirror | migration cycle и compatibility report |
| Юридическая допустимость реального взыскания | не заявлена кодом; требуется утверждённый регламент и независимая проверка | открытый production governance gate |

## Slice 31 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Нет float в количестве, оценке и покрытии | frontend decimal strings -> `BigInt` coefficient/scale; backend `Decimal`; PostgreSQL `Numeric` | unit boundary tests и repository scan |
| Точные суммы и остатки | `decimalAdd`/`decimalSubtract` во всех агрегирующих представлениях | `0.1 + 0.2`, `Numeric(38,12)` carry и component regression |
| Точные пределы и знаки | `decimalCompare`, `decimalMin`, positive/negative predicates | значения выше safe integer и отрицательные дроби |
| RU/EN без потери цифр | строковое grouping и locale separators, half-up только для показа | RU/EN large-value test |
| Защита от возврата ошибки | static source contract запрещает `Number`/`parseFloat` для business decimal fields | `decimal-boundary.test.ts` |
| Совместимость | wire-format, OpenAPI и revision не менялись; отсутствующее старое `executed_amount` трактуется как `0` | RiskView regression и production build |

## Slice 32 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Партия прослеживается до получателя | immutable `FulfillmentProvenance` связывает lot/right/redemption/fulfillment и creditor | PostgreSQL integration и private API |
| Нельзя подменить товар или единицу | product, unit, cooperative и debtor owner сверяются транзакционно | mismatch service tests и полный demo trace |
| Нельзя повторно использовать отпуск | unique `redemption_id` плюс row lock и service rejection | `FULFILLMENT_SOURCE_ALREADY_USED` regression |
| Источник существовал до исполнения | `completed_at <= performed_at`, завершённые статусы и совпавшее signed event | integration assertions |
| Приёмка продолжает цепочку | acceptance actor обязан быть creditor, количество сохраняется отдельно | demo partial acceptance `6` из `8` |
| Приватность доказательств | стороны, перевозчик и назначенные роли; посторонний получает пустую выборку | API principal override test |
| История не переписывается | reconciliation требует роль, rationale, evidence и новое signed event | demo legacy reconciliation и migration guard |
| Проверяемое представление | canonical payload получает `sha256:` proof hash | API format assertion и journal verification |
## Slice 33 trace

| Требование | Сериализация | DB-backstop | Конкурентная проверка |
|---|---|---|---|
| Нет двойного выпуска | lot/balance row locks и expected version | allocation check, unique right-reservation | один success, один `VERSION_CONFLICT` |
| Нет двойного погашения | redemption/right/lot/balance row locks и status | unique open redemption и movement event | один completion, один `REDEMPTION_NOT_PENDING` |
| Нет двойного резервирования | account/bucket/snapshot locks и exact available bound | partial unique indexes и balance checks | risk, solidarity и crisis races |
| Нет двойного execution | transfer/cycle/accounts locks и expected version | active-case, proof и receipt uniqueness | один settlement/finalize, один conflict |
| Повтор после ответа | command registry по actor/type/key/payload | unique command key | replay либо fail-closed conflict |
| Один доказательный след | event append в той же транзакции | unique event refs и chain sequence | journal verification |

## Slice 34 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Физический исполнитель | validated member/user/role snapshot в `performed_by` | forged actor и inactive role integration |
| Действие от имени стороны | typed member/cooperative/node `on_behalf_of` совпадает со scope | forged cooperative rejection |
| Доказательства | непустой canonical evidence list и SHA-256 digest | missing/conflicting evidence rejection |
| Ограниченная экспозиция | category/effect/subject/exact decimal/unit/max loss/basis refs | invalid exposure tests и доменные E2E |
| Approvers и attesters | role-bound local parties и signed remote node references | clearing, compensation, solidarity, fulfillment, federation flows |
| Следующий ответственный | member/cooperative/node list, включая все affected nodes | payload snapshot assertions |
| Нельзя забыть assurance | AST связывает 24 registry types со всеми append call sites | `test_command_assurance_registry.py` |
| История не переписывается | новый v2, старые v1/missing events остаются legacy evidence | journal hash/signature verification |
| Понятная ошибка | RU/EN safe message вместо internal code/request id | frontend unit test |
| Все critical commands | ещё не заявлено: authority/security/custody scope открыт | production-readiness checkbox остаётся пустым |
## Slice 35 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Recovery имеет личного requester и независимого decider | `IDENTITY/REQUEST|EXECUTE|REJECT`, actor-bound attester/approver | identity security E2E и signed payload assertions |
| Recovery возвращается целевому человеку | executed event указывает target member в `next_responsible` | target member assertion |
| Break-glass ограничен человеком, ролью, scope и временем | `AUTHORITY/REQUEST|CREATE|REJECT|REVOKE`, duration/evidence basis | activate/revoke E2E и запрет делегирования |
| Технический user не получает безымянную власть | authority target обязан иметь member | `_user_party` fail-closed guard |
| Старый хранитель остаётся до acceptance | lot custodian меняется только вместе с accepted transfer event | custody E2E before/after assertions |
| Каждая партия имеет hold и transfer exposure | `CUSTODY/HOLD|TRANSFER|RELEASE`, exact quantity/unit | signed lot payload assertions |
| Лимит физической ответственности видим | source/target assignment maximum loss и unit входят в assurance | `500.0000 SHARE` assertion |
| Отказ не оставляет шаг без владельца | reject/decline/block/release возвращают cooperative next party | AST registry и service call sites |
| Нельзя забыть новый assurance | registry расширен до 40 literal event types | `test_command_assurance_registry.py` |
| Все critical commands | ещё не заявлено: role admin, sanctions/crisis и node authority открыты | production-readiness checkbox остаётся пустым |

## Slice 36 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Полномочие получает человек | target user обязан ссылаться на active member | memberless target получает fail-closed отказ |
| Исполнитель действует постоянной ролью | actor user/member/assignment/scope повторно проверяются journal | service/API integration |
| Обычная роль имеет явного владельца | immediate activation передаёт `next_responsible` target member | activated event assertion |
| Привилегированная роль имеет dual control | requester attester и другой approver подписаны вместе | approve/reject integration |
| Отказ и отзыв не оставляют authority без владельца | следующий шаг возвращается cooperative/node scope | rejected/revoked assertions |
| Audit не заменяет доказательство | signed event и state mutation находятся в одной транзакции | journal payload и signature chain |
| Нельзя забыть assurance | registry расширен до 45 literal event types | `test_command_assurance_registry.py` |
| Все critical commands | ещё не заявлено: sanctions/crisis и node authority открыты | production-readiness checkbox остаётся пустым |

## Slice 37 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Санкция имеет процессуальную цепочку | requester/decider/subject, evidence, effect и next owner входят в assurance | trust appeal E2E |
| Апелляция не стирает историю | revoke/correction создают новые signed events | immutable decision/reputation tests |
| Crisis authority ограничена | proposer/activator/controller, mandate scope и safe close подписаны | crisis drill |
| Количество кризисного ресурса точно | Decimal amount и unit для target, snapshot, plan и issuance | signed payload assertions |
| Нулевой остаток не становится ложной экспозицией | zero остаётся фактом payload без positive exposure | service guard |
| Нельзя дважды зарезервировать запас | snapshot lock и повторная проверка available | crisis concurrency |
| Нельзя забыть assurance | registry расширен до 87 literal event types | `test_command_assurance_registry.py` |
| Все critical commands | ещё не заявлено: node authority открыт | production-readiness checkbox остаётся пустым |

## Slice 38 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Внешний узел не является безымянной стороной | local node действует от своего имени, external node и все active named parties получают next responsibility | federation onboarding E2E |
| Доверие и риск ограничены | contract, bilateral limit, bond и exposure содержат точный maximum loss/amount и unit | signed payload assertions |
| Компрометация ключа не скрывает решение | request, old/new proof, incident и независимый approve/reject входят в assurance | key lifecycle E2E |
| Quarantine/revoke не стирают историю | status и limited rehabilitation создают новые signed events | emergency lifecycle E2E |
| Offline authority привязана к узлу | внешний node обязателен при close, лимиты и reconciliation подписаны | offline epoch assertions |
| Нельзя забыть assurance | registry расширен до 115 literal event types | `test_command_assurance_registry.py` |
| Все critical commands текущего registry | software gate закрыт; legal/operations/pilot gates отдельны | production-readiness checkbox и Slice 38 evidence |

## Slice 39 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Acceptance 122 не допускает неиспытанный ARM64 | signed v2 contract квалифицирует ровно одну platform и явно исключает вторую | AMD64/ARM64 positive fixtures и missing-exclusion rejection |
| Образы не смешивают архитектуры | builder и verifier требуют один OS/arch для четырёх runtime roles | mixed-image negative tests |
| Носитель соответствует серверу | `--expected-platform` и Docker host check до `docker load` | expected/host mismatch tests |
| Импорт не подменяет platform | после load повторно проверяются content ID и OS/arch | verifier load contract |
| Offline документация воспроизводима | Linux/PowerShell wrappers принимают qualified/expected platform | parser и shell syntax gates |
## Slice 40 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Acceptance 126: source не содержит закрытых ключей и открытых credentials | Git inventory и strict redacted scan | 735-file live scan и token/PEM/literal fixtures |
| Node payload не проносит secret literal | strict scan всех поставляемых файлов | real payload scan и literal fixture |
| Images не содержат key/secret material | разбор Docker-save outer tar и каждого layer | четыре real images и infected-layer tamper |
| Подписанный отчёт нельзя подделать | verifier повторно сканирует payload/images и сравнивает scope summary | signed false-PASSED rejection |
| `.env` не хранит plaintext secret | production принимает только безопасный `*_FILE` reference | runtime positive/negative tests |
| БД хранит password/token/MFA безопасно | read-only SQL: Argon2id, SHA-256 digests, AEAD nonce/ciphertext, content scan | live PASS и tampered DB rejection |
| Backup не скрывает plaintext | backup v2 сканирует dump, раскрывает blobs и хранит SQL evidence | real backup/restore drill |
| Значение не попадает в диагностику | findings ограничены rule/path/offset или schema/table/column | explicit no-value-leak assertions |

## Slice 41 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Acceptance 127: event/signature/outbox commit вместе | deferred PostgreSQL constraint trigger и unique event indexes | incomplete commit получает `23514`, counts не меняются |
| Доменное состояние откатывается вместе | один application session/commit для responsibility state, audit, event и outbox | injected audit failure и DB guard rollback assertions |
| Worker не является critical dependency | durable `PENDING`, lease и `FOR UPDATE OF outbox_messages SKIP LOCKED` | operation committed при остановленном worker |
| Crash не оставляет половину projection | receipt и `PUBLISHED` в одной worker transaction | rollback после dispatch возвращает attempt/status/receipt |
| Два workers не повторяют эффект | row lock плюс unique `(event_id, consumer_name)` | concurrent restart: один claim, одна receipt |
| Повреждение не публикуется | full envelope validation и quarantine | tampered hash, verifier failure, zero receipt, recovery |
| История проверяется независимо | verifier начинает со всех events и проверяет signature/outbox cardinality | live `434/434/434`, failures `[]` |
| Миграция безопасна для истории | preflight до установки trigger | populated `2921` event downgrade/re-upgrade cycle |

## Slice 42 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Acceptance 128: browser draft не получает `event_id` | versioned local schema с `draft_id`, recursive authoritative-field denylist | pure schema и nested tamper tests |
| Draft не меняет хозяйственное состояние | save использует только IndexedDB, API остается `NetworkOnly` | upload/publish mocks не вызваны, live events `434 -> 434` |
| Статус понятен пользователю | `authoritative=false`, `review_required=true`, явный RU/EN local status | component assertions и live browser UI |
| Нет автоматического replay | reconnect только включает отдельную кнопку публикации | zero publish до explicit click |
| Черновик изолирован между логинами | owner `user_id` index и повторная owner validation | owner mismatch rejection |
| Поврежденный IndexedDB не становится командой | format/owner/expiry/authority validation при каждом чтении | contaminated record rejected |
| Черновик переживает перезагрузку | native IndexedDB и Blob attachment | Docker browser save/reload/review |
| Успешная публикация завершает local lifecycle | delete только в mutation `onSuccess` | explicit publish/delete component test |

## Slice 43 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Acceptance 129: восстановленные DB и journal согласованы | полный `verify_journal` внутри restore consistency gate | isolated restore: 434 events, failures `[]` |
| Установлен тот ключ подписи, которому доверяет БД | Ed25519 fingerprint и public key сравниваются с единственным active record | live key match и wrong-seed unit test |
| MFA material не потерян | каждый зашифрованный TOTP seed фактически расшифровывается | live `3/3`, wrong-key rejection |
| Все связанные вложения пригодны | canonical key, AES-GCM tag, plaintext size и SHA-256 проверяются для каждого READY record | live `55/55`, one-byte tamper rejection |
| Нет скрытых/лишних evidence blobs | disk `.ccb` set сравнивается с DB-derived set | orphan fixture и live orphan `0` |
| Повреждённый restore не открывается пользователям | gate идёт после bootstrap и до запуска API/worker/frontend/gateway | restore script order и nonzero exit |
## Slice 44 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Acceptance 130: неверная подпись не устанавливается | Ed25519 verification предшествует image load, backup и migration | tampered signature, unchanged `s44-old@0037` |
| Неизвестная версия не устанавливается | manifest version и compatibility format имеют закрытый allowlist | корректно подписанный version `3` отклонён |
| Неподдерживаемый переход не устанавливается | exact signed source release/schema allowlist | unit transition mismatch и CI verifier flags |
| Rollback возвращает прежнее приложение | previous bundle повторно проверяется и его image IDs загружаются заново | реальные образы commit `48701b0`, health release `s44-old` |
| Rollback возвращает прежнюю schema | downgrade исполняется target migration image до выбора old app | `0038_atomic_event_outbox -> 0037_actor_assurance` |
| События после backup не теряются | writers quiesced; journal checkpoint сравнивается до/после | сделка и signed event 267 сохранились с тем же hash |
| Старый app не блокирует новый backup gate | target backend используется только как verified read-only consistency verifier | bootstrap defect reproduction и успешный повтор |

## Slice 45 trace

| Требование | Реализация | Проверка |
|---|---|---|
| API `event_id` является событием | ответ хранит UUID `journal.signed_events`, а audit содержит `signed_event_id` | create/replay/update/archive integration assertions |
| Адрес нельзя изменить без происхождения | `last_event_id`, tracking CHECK, deferred FK и mutation trigger | прямой UPDATE получает SQLSTATE `23514` |
| Смена default не оставляет скрытую mutation | затронутые строки блокируются и получают тот же заранее выделенный event UUID | один transaction и signed payload affected IDs |
| Падение вторичного audit не оставляет state/event | state, event, signature, outbox, audit и idempotency в одной транзакции | injected audit failure и нулевые остаточные записи |
| Ответственность персональна | member on-behalf-of/attester, permanent role, active membership и next responsible | critical assurance payload assertions |
| PII не становится вечным journal payload | address/contact/phone/instructions/label остаются в private table | negative immutable payload assertions |
| Rollback схемы не теряет события | downgrade удаляет только адресную ссылку, signed journal остаётся append-only | `0038 -> 0039 -> 0038 -> 0039` count/hash drill |

## Slice 46 trace

| Требование | Реализация | Проверка |
|---|---|---|
| Acceptance 131: health доступен без Интернета | локальный gateway и operator probe в internal `edge` | live/ready `LIVE/READY` при blocked TEST-NET egress |
| Метрики доступны только оператору | штатный login, смена bootstrap-пароля, role-protected operations API | snapshot/readiness и четыре обязательные Prometheus family |
| Журналы доступны локально | bounded `docker compose logs` для api/worker/gateway | непустые bytes/lines/SHA-256 и required service markers |
| Внешняя телеметрия не обязательна | все четыре сети internal, exporter отсутствует | network inspect и `telemetry_export=DISABLED` |
| Секреты не становятся evidence | probe не печатает password/token/raw metrics и сканирует runtime log | unit leak tests для исходного и сменённого пароля |
| Доказательство переносимо | report/network/log плюс LF `SHA256SUMS` | PowerShell и Bash Docker drills, `sha256sum -c` |