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
- одна роль не создаёт две независимые подписи;
- compensation не удаляет исходное событие;
- permutation input не меняет clearing result.

## Concurrency tests

- два клиента резервируют последний остаток;
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
