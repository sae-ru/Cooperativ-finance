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
