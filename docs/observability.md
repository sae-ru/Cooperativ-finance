# Наблюдаемость

Статус: локальный production baseline без обязательного SaaS.

## Сигналы

1. Structured logs.
2. Metrics.
3. Health/readiness.
4. Audit trail.
5. Integrity reports.

Audit не заменяет operational log, а log не заменяет signed event journal.

## Structured log

Обязательные поля: timestamp UTC, level, service, release, node_id,
request_id, trace_id, actor pseudonymous id, route/use_case, duration_ms,
result_code. Для worker: job id, event id, attempt и queue latency.

Запрещены password, token, private key, full PII, medical details, complete
signed payload и blob content.

## Метрики API

- request count/latency/error по route template и status class;
- active DB connections и pool wait;
- transaction retries, deadlocks и serialization failures;
- idempotency hits/conflicts;
- critical command success/failure по safe error category;
- upload bytes/rejects без filename/PII labels.

## Доменные safety metrics

- negative balance attempts;
- over-reservation rejects;
- double redemption rejects;
- unresolved disputes и oldest age;
- pending dual approvals и expiry;
- active share exposure по агрегированным контурам;
- inventory discrepancies;
- clearing preview/finalize conflicts;
- crisis policy expiry;
- missing responsibility chain attempts.

Доменные метрики не используются как скрытая репутационная формула.

## Worker и sync

- outbox ready/processing/quarantined;
- oldest outbox age;
- inbox received/rejected/conflict;
- source sequence gap;
- package verify duration и size;
- blob integrity failures;
- last successful sync per trusted node;
- certificate/key expiry и revocation freshness.

## Backup и host

- last successful complete backup;
- last verified restore;
- WAL archive lag;
- disk free/inodes;
- clock drift;
- UPS/power signal при доступности;
- certificate expiry;
- release signature/integrity status.

## Health endpoints

- `/health/live`: процесс отвечает, без глубокой зависимости;
- `/health/ready`: DB, schema, key access, required blob path;
- `/health/details`: только admin scope, компонентные проверки без secrets;
- `/metrics`: только локальная management network и auth policy.

Readiness не проверяет внешний Интернет или federation как обязательные.

## Alerts

Critical: event integrity failure, invalid release, lost DB, key compromise,
negative invariant, backup overdue, disk exhaustion, repeated invalid package.

High: worker quarantine, sequence gap, certificate expiry, unresolved custody
conflict, restore test overdue.

Warning: sync stale, increased retries, nearing capacity, role/policy expiry.

Alert содержит runbook link, node, first/last seen и safe context. Получение
alert не зависит только от внешней почты: он виден в GUI и локальной консоли.

## Retention

Operational logs имеют ограниченный срок и rotation. Audit и signed events
хранятся по отдельной policy. Уменьшение observability storage не удаляет
хозяйственную историю.

## Диагностический bundle

Локальный оператор может сформировать зашифрованный bundle с manifest,
операционной сводкой, готовностью host и metrics snapshot. Логи, private keys,
токены, signed payload и raw PII не включаются. Перед экспортом показывается
точный закрытый перечень файлов.
## Реализованный локальный baseline

`GET /api/v1/operations/snapshot` и `GET /api/v1/operations/metrics` доступны
только ролям `COOPERATIVE_ADMIN`, `SECURITY_ADMIN` и `AUDITOR`. Метрики имеют
Prometheus text format и используют только bounded labels: route template,
method и status class. In-memory HTTP counters обнуляются при рестарте процесса;
хозяйственная история и signed journal от них не зависят.

`coopctl diagnostics` выдаёт PII-free JSON snapshot для локального runbook.
Раздел GUI `Эксплуатация` обновляет его раз в 30 секунд. Полный локальный
release evidence собирается `scripts/collect-production-evidence.sh` или `.ps1`;
raw logs не включаются по умолчанию. Реализация и ограничения зафиксированы в
[Slice 12](implemented_slice_12.md).

## Peer-сигналы Slice 13

Операционный контур должен показывать без payload/PII: peer requests по
operation/result, timeout/size/signature/replay rejects, fan-out partial
results, active/expiring home-node holds, intents в `COMMITTING` и
`CANCELLING`, oldest saga age, reserved/current exposure и приближение к
bilateral limits. Alert на недоступность peer не делает локальный node
неготовым, но блокирует соответствующую внешнюю команду и даёт runbook context.

## Сигналы межузлового клиринга Slice 14

Наблюдаемость должна показывать без хозяйственного payload: число циклов по
state, oldest prepare, prepare expiry, snapshot/approval/apply readiness по
bounded node code, certificate age, `COMMITTED_PENDING_APPLY`, lagging nodes,
recovery attempts, signature/hash rejects и приближение к bilateral exposure.

Alert после commit имеет более высокий приоритет, чем недоступность до commit:
certificate уже означает экономическую финальность и требует доведения apply.
Недоступность peer не делает локальный `/health/ready` красным, но блокирует
новый межузловой переход, которому этот peer необходим.

## Локальная готовность и диагностика Slice 29

`GET /api/v1/operations/host-readiness` объединяет локальные и серверные сигналы:

- свободное место blob volume и host filesystem;
- расхождение часов приложения и PostgreSQL, плюс host sync status;
- возраст и тип последней завершённой резервной копии;
- просроченные и приближающиеся к замене сертификаты;
- штатное питание, работа от батареи и низкий заряд ИБП.

Host marker-файлы ограничены 64 КиБ, имеют versioned format и читаются API из
read-only mount `.operations`. Probe старше 180 секунд не считается текущим.
Пороговые значения задаются environment variables и проверяются при запуске.
В hardened-среде отсутствующая backup/UPS информация не превращается в зелёный
статус: итог будет `ATTENTION` или `CRITICAL`.

`GET /api/v1/operations/metrics` публикует `coop_host_readiness` и
`coop_host_check_severity` с пятью фиксированными именами. Ни пользовательские
идентификаторы, ни пути, ни payload не используются как labels.

`POST /api/v1/operations/diagnostic-bundle` выполняется только ролями
`COOPERATIVE_ADMIN`, `SECURITY_ADMIN`, `AUDITOR`, принимает passphrase как
`SecretStr`, строит пакет вне event loop и возвращает только зашифрованный
`.ccdiag` с `Cache-Control: no-store`. Успешная выдача обязательно создаёт
append-only audit с actor, request ID, размером и SHA-256 ciphertext. Формат,
проверка и ограничения описаны в [Slice 29](implemented_slice_29.md).

## Изолированный контракт Slice 46

`scripts/test-local-observability.sh` и `.ps1` проверяют весь локальный контур в
Compose-топологии без внешнего маршрута. Все четыре сети имеют `Internal=true`;
read-only probe внутри `edge` получает health, защищённые snapshot/readiness и
Prometheus metrics, а gateway не достигает TEST-NET адреса.

Отчёт содержит только агрегаты, schema, hashes и статусы. Пароль, access token,
сырое тело metrics и runtime log в него не входят. Сам bounded log остаётся
локальным evidence и по-прежнему исключён из diagnostic bundle. Подробности:
[implemented_slice_46.md](implemented_slice_46.md).