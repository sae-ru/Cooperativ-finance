# Стратегия тестирования

Статус: обязательный quality contract.

## Принцип

Тесты доказывают инварианты и восстановимость, а не только успешные HTTP-коды.
Критический defect не закрывается без regression test на правильном уровне.

## Уровни

| Уровень | Что проверяет | Внешние зависимости |
|---|---|---|
| Domain unit | transitions, calculations, policies | нет |
| Property/state machine | общие инварианты и последовательности | нет |
| Repository integration | SQL constraints, locks, mappings | real PostgreSQL |
| Application integration | transaction, event, audit, outbox | real PostgreSQL/blob adapter |
| API contract | OpenAPI, auth, idempotency, errors | test app + PostgreSQL |
| Frontend component | формы, таблицы, states, accessibility | mocked typed API boundary |
| E2E | role workflows через браузер | полный local node |
| Protocol | canonicalization, signatures, package import | golden vectors |
| Recovery | backup, restore, upgrade, rollback | production-like host |
| Field drill | люди, устройства, бумага, outages | pilot environment |

SQLite не используется как замена PostgreSQL в integration tests.

## Обязательные property tests

- quantity и balance не отрицательны;
- issued rights <= verified backing;
- redemption суммарно <= issued quantity;
- fulfillment суммарно <= obligation;
- clearing сохраняет суммы и границы;
- share execution <= finalized loss <= reserved max exposure;
- protected amount не взыскивается;
- solidarity contribution не меняет credit/reputation;
- rationing не превышает physical verified available и не создаёт debt/reputation;
- protected minimum применяется до weighted priority;
- одна роль не создаёт две независимые подписи;
- compensation не удаляет исходное событие;
- permutation input не меняет clearing result.

## Concurrency tests

- два клиента резервируют последний остаток;
- два rationing confirm резервируют один verified crisis stock;
- два погашения одного права;
- finalize при изменённом obligation version;
- два approvers финализируют одно решение;
- два workers берут одну outbox row;
- duplicate sync import;
- share reservation и release одновременно;
- role revoked между prepare и finalize.

Тесты запускаются многократно и проверяют конечное состояние, журнал и число
side effects.

## Security tests

- authorization matrix role/scope/object;
- IDOR для всех object endpoints;
- session revoke и step-up expiry;
- signature, key validity, revocation timeline;
- replay и tampered duplicate;
- malformed archive, zip slip, decompression bomb;
- upload type/size/hash;
- CSRF/CSP/CORS/rate limits;
- secret и dependency scans;
- audit отсутствия sensitive values.

## GUI tests

- desktop/mobile viewport matrix;
- keyboard-only critical workflows;
- screen reader names и focus order;
- loading/error/empty/offline/conflict/expired states;
- длинные русские и английские строки;
- большие quantities/identifiers без layout shift;
- status не зависит только от цвета;
- draft никогда не выглядит accepted;
- screenshot regression для ключевых рабочих мест;
- browser compatibility на реальном пилотном оборудовании.

## Protocol golden vectors

В репозитории хранятся canonical bytes, hashes, signatures, valid/invalid
packages, old version fixtures и expected conflict classifications. Fixtures не
содержат production keys/PII.

## Migration tests

- пустая БД до head;
- предыдущий production schema до head;
- upgrade на объёме выше пилотного;
- downgrade или recovery plan;
- interrupted migration;
- старые signed payload не меняются;
- read models перестраиваются;
- приложение прежней версии корректно блокируется при несовместимой схеме.

## Recovery tests

Ежемесячно: clean host, offline bundle, полный restore, event verification,
blob verification, local login, critical read/write smoke, measured RPO/RTO.
Перед релизом с migration выполняется pre-upgrade backup и rollback drill.

## Release gates

- format/lint/type checks;
- unit/property/integration/API/frontend/E2E green;
- architecture dependency tests;
- migration and OpenAPI compatibility report;
- security scans без незакрытых critical/high либо formal accepted risk;
- SBOM и license compatibility;
- signed artifacts и reproducibility record;
- backup/rollback evidence;
- updated docs/ADR/runbook;
- manual approval для production package.

Flaky test является defect. Его нельзя бессрочно перезапускать до зелёного.

## Проверки Slice 11

Обязательный federation-набор включает lifecycle onboarding/contracts/limits,
role separation, package canonicalization/signatures, import idempotency,
blocking conflicts, exposure bounds, key revoke/rotation timeline, incident
quarantine, paper form checksum/serial/QR uniqueness и PostgreSQL immutability.
Integration demo проходит production services и повторный seed без новых
событий.

Эксплуатационный gate дополнительно выполняет `bash -n`/PowerShell parser,
координированный backup, checksum/archive verification и restore в одноразовые
DB/blob volumes. Текущий verified baseline: backend 129 tests и 78,41% coverage;
frontend 103 tests и 83,08% statement coverage; strict mypy и production PWA
build зелёные.

## Проверки Slice 13

Обязательный набор включает canonical request/response signatures, short TTL,
source/target/capability binding, replay и altered replay, bounded peer
transport, partial fan-out, deterministic landed cost, stale/revoked filtering,
oversell concurrency, separate goods/logistics holds, bilateral exposure,
idempotent reserve/commit/release, durable saga recovery и worker expiry.

Текущий verified baseline: fresh migration до `0017_peer_reservations`,
`alembic check`, Ruff/format, strict mypy 213 files, backend 143 tests с 76.08%
coverage, frontend 45 files/108 tests с 70.29% branch coverage, generated
OpenAPI types и production PWA build.

## Проверки Slice 14

Обычный backend gate запускается с `pytest -m "not acceptance"`; multi-node
acceptance имеет отдельную topology и запускается `scripts/test-federation.sh`
или `.ps1`. Это исключает случайное подключение acceptance к одной тестовой БД.

Обязательный набор проверяет deterministic netting, canonical signatures,
changed-byte rejection, snapshot/prepare/approval coverage, bilateral exposure,
certificate finality, идемпотентный local apply, coordinator/participant outage,
lagging-node recovery и точную reconciliation трёх независимых PostgreSQL.
Frontend gate проверяет API contracts, role-specific commands, evidence table,
финальность, recovery и формы policy/obligation/cycle.

Контрольный frontend baseline: 47 files / 115 tests, 82.50% statements, 70.87%
branches, 75.83% functions, 88.67% lines; typecheck и production PWA build
зелёные. Трёхузловой acceptance: `1 passed in 11.05s`.

Backend baseline: Ruff, strict mypy 223 files, 158 tests и 75.16% coverage;
один multi-node acceptance test выделен в отдельную Docker topology.

## Проверки Slice 15

Release gate запускает `scripts/tests/test_release_bundle.py`. Девять негативных
и позитивных тестов проверяют подпись, expected release, independently pinned
license policy, altered archive, extra file, path traversal, wrong key,
blocked license и ложную классификацию.

Живая приёмка дополнительно создаёт bundle из четырёх runtime-образов, выполняет
verify с повторным `docker load`, копирует только signed `node/` payload и
запускает новый Compose project с пустыми volumes и `--pull never --no-build`.
Gate считается пройденным после healthy всех постоянных services, первого
локального входа и `coopctl verify-journal`.
## Проверки Slice 16

Операционный drill использует два signed release id и отдельный Compose project.
Обязательная последовательность: r1 health, FULL backup, target verify/load,
migration, injected failure, automatic rollback, повторный successful update,
independent restore drill и destructive restore previous release.

Проверяются exact release selection, node id, schema, runtime ACL, table/event/
blob counts, application permissions, bounded readiness и signed journal.
Тестовые release могут разделять один immutable image ID: так проверяется именно
state machine операции. Migration gate разных production schemas остаётся
отдельным обязательным тестом.
## Проверки Slice 17

scripts/test-critical-quality.sh объединяет четыре независимых gate: OpenAPI
compatibility, migration предыдущей схемы, deterministic property matrix и
повторяемые PostgreSQL concurrency scenarios. По умолчанию выполняются три
раунда; один retry после падения не считается успехом.

Проверенный baseline: OpenAPI 298 операций и exact mirror; 200 local и 100
federated generated graphs; переход 0017 -> 0018, downgrade и re-upgrade с
сохранением identity; три раунда по 21 critical test; итоговый signed journal
556/556 без failures. Подробности:
[implemented_slice_17.md](implemented_slice_17.md).
## Проверки Slice 20

Unit-тесты проверяют AES-256-GCM round-trip и отказ при подмене ciphertext,
TOTP window и replay counter. Интеграционный сценарий проходит enrollment,
step-up, recovery с двумя сотрудниками, отзыв прежних сессий и фактора,
break-glass activation/use/revoke, подписанные события и запрет делегировать
постоянную роль из временного полномочия. Миграционный gate выполняет populated
цикл `0024 -> 0025 -> 0026 -> 0027 -> 0026 -> 0025 -> 0024 -> 0027`.

Frontend gate проверяет QR enrollment, понятные RU/EN ошибки и независимые
решения recovery/break-glass. Обязательны typecheck, production build и
визуальная проверка раздела **Безопасность** в RU/EN на desktop и mobile.
WebAuthn, независимый security review и физическое учение восстановления
остаются отдельными production gates.
## Проверки Slice 21

Domain/unit gate проверяет полноту enum/манифеста, стабильность SHA-256,
положительный и отрицательный сценарий каждого нового класса, транзитивные
связи, временные окна, actor binding помощи и репутации, достижимый порог
дробления лимита и пересечение подгруппы кампаний.

PostgreSQL integration проверяет algorithm/manifest/dataset provenance,
неизменяемый сигнал, реальную блокировку команды и independent evidence-backed
release. API snapshot требует `GET /api/v1/antifraud/rules` с 13/15,
`SYNTHETIC_REGRESSION` и `production_approved=false`. Migration gate проходит
`0027 -> 0028 -> 0027 -> 0028`, legacy backfill и `alembic check`.

Frontend gate проверяет XML parity, русские и английские названия всех
requirement/reason/fact/threshold keys, предупреждение о незавершённой пилотной
калибровке, generated types, component suite и production PWA build. Реальная
precision/recall/FPR измеряется только на отдельно утверждённом пилотном наборе
и не может быть заменена зелёными синтетическими тестами.

## Проверки Slice 22

Domain unit tests фиксируют матрицы переходов Cooperative, Membership и User.
PostgreSQL integration проходит полный lifecycle, optimistic version, scoped
registrar lists/overview, запрет чужого cooperative и совместимый single-scope
Member request. Отдельно выдаётся refresh session и доказывается её немедленный
отзыв в той же транзакции, где User получает `DISABLED`.

Frontend component tests проверяют пять раздельных вкладок, создание организации,
переход membership, отключение учётной записи и доступ к node management.
Migration gate выполняет `0028 -> 0029 -> 0028 -> 0029`; OpenAPI gate требует
exact frontend mirror и отсутствие несовместимостей с baseline.

## Проверки Slice 26

Unit-тесты покрывают нормализацию входа и reference groups, fail-closed разбор снимка, blockers по отсутствию, версии и статусу Member/User/Membership, постоянство персональных ролей, cooperative scope, idempotent replay и формирование case view. PostgreSQL integration проходит полный lifecycle создания, немедленного отзыва сеансов, независимого решения, событий, аудита и повторного idempotency response.

Обязательный gate включает migration cycle `0032 -> 0033 -> 0032 -> 0033`, `alembic check`, равенство backend/frontend OpenAPI, RU/EN XML symmetry, component tests административного экрана, production PWA build и браузерную проверку обоих языков и темы. Для корректной трассировки async SQLAlchemy через greenlet coverage запускается с `concurrency = ["greenlet", "thread"]`.

## Проверки Slice 27

Модульные тесты проверяют сроки, ссылки на основания, постоянные роли, количество и fail-closed blockers. PostgreSQL integration проходит цепочку исходное назначение -> containment -> hold -> независимый пересчет -> одобрение -> личная приемка и отдельно доказывает блокировку обычной складской команды. Frontend-тесты проверяют RU/EN, ролевые действия, обязательные акты и optimistic versions. Миграционный gate включает минимальные права runtime-роли и цикл `0033 <-> 0034`.

## Проверки Slice 28

Pure Python tests проверяют precedence process/`.env`, точные environment values, atomic rewrite без дублей, fresh production, запрет demo promotion, known demo credentials и hardened downgrade. Static contract tests требуют одинаковые signed-bundle/no-build guards в Windows/Linux wrappers, сохранение operational artifacts, их чтение backup/update scripts и отсутствие alias `prod`.

PowerShell scripts проходят parser, Linux scripts - `sh -n`. PostgreSQL integration отдельно доказывает отказ при `demo_data_loaded`, отказ in-place hardened transition и допустимый повторный старт fresh production profile. Release bundle suite включает `runtime_environment.py` в подписанный node payload. Живой demo regression должен подтвердить `environment=dev`, `demo_data_loaded=true`, `OPERATIONAL`, `RUNNING` и отсутствие browser regressions.

## Проверки Slice 29

Backend unit tests проверяют расчёт пяти состояний узла, границы порогов,
устаревший probe, детерминированный plain ZIP, AES-256-GCM/scrypt round-trip,
неверную passphrase и изменение ciphertext. PostgreSQL integration проверяет
RBAC трёх эксплуатационных ролей, readiness, план содержимого, binary download,
Prometheus host metrics и audit `DIAGNOSTIC_BUNDLE_EXPORTED` без passphrase.

Pure Python script suite проверяет atomic bounded marker-файлы, регистрацию
только завершённой FULL/DATA_ONLY копии, цикл фонового probe, повторный запуск,
сверку `monitor_id` перед остановкой, точный ZIP inventory, duplicate names,
oversized input, manifest SHA-256 и отказ при неверном пароле. Отдельный живой
Windows smoke обязан доказать start -> owned marker -> stop -> process absent;
Linux shell syntax проверяется в контейнере Alpine, PowerShell - встроенным
parser API.

Frontend gate проверяет типизированные API, binary POST, понятные RU-статусы без
внутренних `BACKUP_DATA_ONLY`/`NOT_CONFIGURED`, совпадение passphrase, XML parity,
полную английскую эксплуатационную страницу без кириллицы, PWA recovery после
устаревшего lazy chunk, TypeScript и production build. OpenAPI gate требует 363
операции, совместимость с baseline и byte-exact frontend mirror.

Проверенный checkpoint: Ruff, strict mypy по 262 source files, `249 passed, 1
deselected` и line coverage `77.29%` при обязательном пороге 75%; 41 script
tests; 66 frontend files / 186 tests и coverage `81.82%` statements / `70.77%`
branches / `75.08%` functions / `87.48%` lines; production PWA build; 21
critical tests, migration cycle `0033 <-> 0034`, signed journal verification и
трёхузловой acceptance `1 passed`. Browser smoke прошёл RU/EN, light/dark и
desktop/mobile без смешения языков и горизонтального overflow.

CI job обязан собирать frontend с repository root context и
`-f frontend/Dockerfile`, потому что build contract читает корневой `/lang`.
Migration job использует текущий переход `0033_member_continuity` ->
`0034_custody_continuity`, secret `secrets/postgres_migrator_password` и TCP
readiness внутри Compose network. Локальный эквивалент не закрывает remote CI
checkbox до зелёного workflow на опубликованном commit.

Эти проверки подтверждают code-level baseline. Они не заменяют длительный
мониторинг на целевом host, физическое испытание ИБП, ручную browser/device
матрицу и независимую проверку диагностического пакета по privacy policy.

## Проверки Slice 30

Backend integration покрывает полный liability -> original decision -> appeal
`AFFIRMED` -> independent authorization -> personal acceptance lifecycle,
запреты всех decision makers, scoped visibility, idempotency, exact balances и
неизменность protected amount. Отдельный repeated-seed test запускает тот же
production demo дважды; `scripts/test-critical-quality.sh` включает и доменный
E2E, и demo regression.

Проверенный checkpoint 28 июля 2026 года: Ruff; strict mypy по `264` source
files; `251 passed, 1 deselected`; line coverage `82.94%` при пороге `75%`;
`67` frontend files / `189` tests; TypeScript typecheck и production PWA build.
Migration cycle `0034 -> 0035 -> 0034 -> 0035` прошёл на пустой БД, а downgrade
БД с compensation history ожидаемо остановился fail-closed. OpenAPI содержит
`367` операций, совместим с baseline и byte-exact совпадает между backend и
frontend. Signed journal verification прошла для `623` событий.

Живой Docker/browser сценарий подтвердил русскую и английскую локали, desktop и
mobile `390x844` без горизонтального overflow и личное принятие `15 DEMO_SHARE`:
source `100 -> 85`, destination `5 -> 20`, protected amount `40 -> 40`, held
`15 -> 0`. Эти проверки не заменяют legal/security review, restore drill и
полевой pilot на целевом Linux host.

## Проверки Slice 31

Frontend unit boundary проверяет точную арифметику десятичных строк, границу
`Numeric(38, 12)`, большие значения вне safe integer, отрицательные величины,
экспоненциальную запись, локали RU/EN и отклонение недопустимого ввода.

Repository contract test загружает production frontend sources и запрещает
`Number(...)`/`parseFloat(...)` для известных полей количества, паёв, цены,
стоимости, ущерба, покрытия, резервов и остатков. Отдельный статический аудит
backend допускает float только в техническом времени, метриках и capacity tool;
хозяйственные модели используют `Decimal`/`Numeric`.

Проверенный checkpoint 28 июля 2026 года: `69` frontend test files, `196`
tests, TypeScript typecheck и production PWA build. OpenAPI и migration cycle не
повторяются как изменившиеся артефакты, потому что срез не меняет wire-format или
схему данных. Пересобранный Docker frontend и пять healthy Compose services
прошли RU/EN desktop/mobile smoke без horizontal overflow и browser console
errors.

## Проверки Slice 32

Отдельный PostgreSQL integration-тест поднимает production demo-команды и
проверяет цепочку `lot -> right -> redemption -> fulfillment -> acceptance`.
Он сопоставляет cooperative, product, unit, owner, debtor, creditor, количества
и signed event IDs, затем пытается повторно использовать то же погашение и
ожидает доменный отказ.

Тот же тест читает private trace API от имени получателя, проверяет SHA-256 и
частичную приёмку, а затем подтверждает, что посторонний участник не видит
запись. В конце заново проверяется весь подписанный журнал узла.

Проверенный checkpoint 28 июля 2026 года: Ruff, strict mypy по `264` source
files, `252 passed, 1 deselected`; `69` frontend test files / `196` tests,
TypeScript typecheck и production PWA build. Живой Docker-узел отвечает
`READY`, имеет revision `0036_fulfillment_traceability`, две provenance-записи
и валидный журнал из `434` событий. Этот технический gate не заменяет
физическую сверку партии и независимую проверку актов в пилоте.
## Проверки Slice 33

В существующий commodity-right integration добавлена реальная гонка двух
выпусков с одной balance version. Проверяются один успешный выпуск, один
`VERSION_CONFLICT` и точные поля `available`/`rights_issued` до и после
конкурентного погашения.

Compensation E2E запускает две одновременные acceptance-команды. Проверяются
ровно один `SETTLED`, один `RISK_VERSION_CONFLICT`, единственный атомарный
debit/credit, неизменный protected amount и повторный отказ через HTTP API.
Вместе с clearing, risk, solidarity и crisis concurrency tests это покрывает
четыре класса exactly-once из production-readiness.

Checkpoint на заново созданной test-БД: Ruff и `252 passed, 1 deselected`.

## Проверки Slice 34

Journal integration намеренно отправляет критическую команду без assurance,
без evidence, с forged cooperative scope и с двумя конфликтующими источниками
evidence. Каждый вариант обязан завершиться доменным отказом до commit.
Положительный сценарий сравнивает полный v2 snapshot и затем независимо
проверяет hash chain и Ed25519 signature.

AST unit gate разбирает все production Python sources, извлекает literal event
types из обычных и conditional expressions и требует `assurance=` для каждого
элемента `CRITICAL_EVENT_TYPES`. Он также требует, чтобы все 24 registry types
были найдены хотя бы в одном реальном call site.

Domain regression повторно проходит rights, fulfillment/provenance, local
clearing, share exposure, compensation, solidarity, federation lifecycle и
inter-node prepare/commit/apply/reconcile. Frontend проверяет безопасный RU/EN
текст отказа без internal code и request id.

Контрольный прогон Slice 34 на чистой test-БД: Ruff без замечаний, strict mypy
по `220` production source files, `255 passed, 1 deselected` backend,
`197 passed` frontend, typecheck, production build и отдельный three-node
Docker federation acceptance `1 passed`.
## Проверки Slice 35

Identity security E2E проходит TOTP, account recovery, независимое решение,
отзыв сессий/factors, break-glass activation, запрет делегирования временной
власти и revoke. Дополнительные assertions читают `_command_assurance` и
проверяют category, node/cooperative scope и target member.

Custody E2E подтверждает, что old custodian сохраняется до личного acceptance,
а затем source assignment освобождается, target assignment активируется и lot
атомарно получает нового custodian. Signed payload assertions проверяют
`CUSTODY`, `HOLD`, maximum loss `500.0000 SHARE`, candidate member и permanent
role assignment.

AST gate теперь требует assurance для 40 событий. Оба E2E включены в
`test-critical-quality.sh`. Контрольный прогон на чистой test-БД:
Ruff, strict mypy и `255 passed, 1 deselected`.

## Проверки Slice 36

Role administration integration проходит privileged request, независимое
approval, immediate activation обычной scoped-роли, independent rejection и
revoke. Signed payload assertions проверяют target member, requester/approver,
node/cooperative scope и каждого `next_responsible`.

API flow использует персонального security administrator и target user,
связанный с active member, затем читает подписанный request event. AST gate
теперь требует assurance для 45 событий. Оба сценария включены в
`test-critical-quality.sh`. Контрольный прогон: Ruff, strict mypy по `220`
production source files, `255 passed, 1 deselected` на чистой схеме, `29 passed`
в independent critical-quality round и `1 passed` в three-node acceptance.
Живой узел вернул `READY`, journal verification подтвердил `434/434` событий.

## Проверки Slice 37

Trust appeal E2E читает signed events для dispute, protective measure,
sanction, appeal, reputation correction и rehabilitation. Crisis drill читает
assurance для reserve target/snapshot, mandate, rationing, resource issuance и
paper forms; отдельный concurrency test проверяет единичное резервирование.

AST gate теперь требует assurance для 87 событий. Оба доменных сценария входят
в `test-critical-quality.sh`. Контрольный прогон: Ruff, strict mypy по `220`
production source files, `255 passed, 1 deselected`, targeted `7 passed`,
independent critical-quality `33 passed`, migration cycle green и journal
verification `452/452`, three-node acceptance `1 passed`, live Docker node
`READY` и его журнал `434/434`. Evidence:
`evidence/quality-20260728T164149Z`.

## Проверки Slice 38

Federation onboarding E2E читает assurance для заявки, пяти принятых
персональных ролей, identity/challenge/audit, trust contract, bilateral limit,
bond, activation, offline epoch и внешней exposure. Emergency lifecycle в
одной откатываемой транзакции проходит incident, плановую и compromise-ротацию
ключей, независимые approve/reject, suspend, quarantine, revoke и limited
rehabilitation.

AST gate требует assurance для 112 событий. Federation flow входит в
`test-critical-quality.sh`. Контрольный прогон: Ruff, strict mypy по `220`
production source files, полный backend `256 passed, 1 deselected`,
independent critical-quality `35 passed`, migration cycle green и journal
verification `452/452`, three-node acceptance `1 passed`, live Docker node
`READY` и его журнал `434/434`. Evidence:
`evidence/quality-20260728T170948Z`.

## Проверки Slice 39

Release bundle tests создают корректные signed AMD64 и ARM64 fixtures и
повторно подписывают намеренно испорченные манифесты, чтобы проверить именно
семантический verifier после успешной криптографической проверки. Missing
platform contract, mixed image architecture, отсутствующее явное исключение,
expected-platform mismatch и несовместимый Docker host отклоняются fail-closed.

Полный script gate: `49 passed`; Python compile, все Bash scripts и все
PowerShell scripts проходят синтаксическую проверку. CI supply-chain job
создаёт v2 bundle с `--qualified-platform linux/amd64` и независимо проверяет
его с `--expected-platform linux/amd64`.

Live release-contract gate: signed bundle `slice39-platform-contract`,
`linux/amd64`, `4` images, `45` node payload files, `blocked=0`; verifier с
`--load-images` успешно импортировал и повторно проверил все образы. Evidence:
`evidence/platform-release-20260728T175332Z`.
## Проверки Slice 40

Secret fixtures покрывают полный PEM block, token signatures, credential URL,
secret filename, strict literal, plaintext `.env`, infected Docker layer,
compiled vendor binary boundary и распакованный blob backup. Release tests
доказывают, что подписанный ложный `PASSED` не скрывает находку.

Strict Git inventory scan прошёл 735 файлов без находок. Реальный signed
`linux/amd64` bundle содержит 4 images, 48 node payload files и 6 clean scopes;
независимый verifier повторно раскрыл payload/images и выполнил `docker load`.
Живая и восстановленная БД прошли `secret_storage=PASS`; tampered DB отклонена
без вывода значения. Backup v2 восстановил schema `0037_actor_assurance`, 149
tables, 434 signed events и 47 blobs.

Полный script gate: `64 passed`; backend: `256 passed, 1 deselected`; затронутые
frontend fixtures: `5 passed`; Ruff, Python compile, все Bash scripts и 12
PowerShell scripts прошли. Evidence:
`evidence/secret-safety-20260728T185835Z`.

## Slice 41: atomic event/outbox gate

PostgreSQL integration tests выполняют deferred commit без signature/outbox,
injected rollback после создания consumer receipt, конкурентный restart двух
workers и повреждение canonical outbox payload. Проверяются exact counts,
status, attempt count, quarantine code, отсутствие receipt и последующее
восстановление. Migration проходит populated `0038 -> 0037 -> 0038`, а live
verifier начинает со всех events и требует полную event/signature/outbox
кардинальность.

## Slice 42: browser draft authority gate

Pure frontend tests запрещают event identity во всем дереве draft payload и
отклоняют tampered IndexedDB record. Component tests переводят браузер в
offline, сохраняют товар без upload/publish API, восстанавливают online и
требуют отдельный review и publish click. Delete локальной записи разрешен
только после успешной server mutation.

Browser gate на полном Docker-стенде записывает signed event count до save,
после save и после reload/review. Все три значения обязаны совпадать. Затем
тестовый local draft удаляется, readiness и console errors проверяются снова.

## Slice 43: restore consistency gate

Unit tests проверяют правильный blob, one-byte ciphertext tamper, orphan `.ccb`,
несовпадающие Ed25519 seed/public record и MFA key. PostgreSQL integration test
создаёт demo evidence, запускает полный journal/key/blob verifier, портит реальный
blob и требует `EVIDENCE_CONTENT_CORRUPT`, возвращая файл в `finally`.

Живой `coopctl verify-restore-consistency`: journal `434`, signing keys `1`,
evidence `55/55`, unique blobs `45`, MFA `3/3`, orphan `0`, failures `0`.
Independent restore drill: schema `0038_atomic_event_outbox`, 149 таблиц, 434
events и 47 файлов архива; восстановленный consistency report `ok=true`.
Синтетический FULL повторил drill с independently verified exact release из
четырёх образов и encrypted recovery fixture; одноразовый signing private key
после проверки удалён.
Финальный backend gate: `261 passed, 2 deselected`, coverage `83.35%`; restore
acceptance отдельно прошёл `1 passed` на чистой Docker-топологии.
## Slice 44: signed update and rollback gate

Release unit tests требуют manifest version `2`, compatibility format v1 и
точную пару `source release@schema`. Wrong signature, correctly signed unknown
version, unsupported source schema, duplicate transition и unsafe rollback mode
должны завершаться до `docker load`.

Migration gate создаёт данные на `0037`, поднимается на `0038`, принимает новые
signed business events, возвращается на `0037` и сравнивает count/last hash до
повторного upgrade. Script contract tests удерживают одинаковые Linux/Windows
проверки, оба signed bundle, target verifier для старого app и journal checkpoint.

Изолированный acceptance использует реальные старые образы commit `48701b0` и
новые образы. После pre-update backup через API создаётся сделка; rollback обязан
вернуть old app/schema и сохранить deal, event sequence и hash. Test key,
DATA_ONLY backup и локальный host не заменяют production ceremony/FULL drill.

## Slice 45: participant address event assurance

`test_participant_address_book.py` проверяет полный create/replay/update/archive
flow и связывает каждый возвращённый event UUID с `SignedEvent`, NODE signature и
outbox. Worker в тесте не нужен для commit. В immutable payload запрещены полный
адрес и телефон.

Injected failure в `AuditRepository.record` обязан откатить address, journal,
signature, outbox и idempotency. Отдельный ORM mutation без смены
`last_event_id` обязан завершиться IntegrityError от PostgreSQL trigger.
`test_command_assurance_registry.py` распознаёт типизированный address wrapper и
требует assurance для всех трёх event types.

Migration gate проходит `0038 -> 0039 -> 0038 -> 0039`, проверяет trigger,
constraint и index, а после downgrade сравнивает точные event count и last hash.
`alembic check` должен возвращать `No new upgrade operations detected`.

## Slice 46: local observability without Internet

Pure Python tests проверяют loopback/internal-origin allowlist, обязательные
metric families, полную internal network evidence, границу размера logs и
запрет утечки исходного либо сменённого пароля. Bootstrap credential меняется
через штатный `/api/v1/auth/change-password`; оба access token остаются только в
памяти процесса.

Linux и PowerShell acceptance wrappers поднимают чистый Compose project, требуют
`Internal=true` для `edge/app/web/data`, доказывают blocked egress к TEST-NET,
собирают bounded local logs и запускают read-only probe внутри `edge`. Итоговые
`report.json`, `network-isolation.json`, `runtime.log` и LF `SHA256SUMS`
проверяются независимо. Target-host UPS/clock/storage и ручной operator drill
этим тестом не подменяются.