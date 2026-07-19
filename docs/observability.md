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
версиями, health, redacted logs и metrics snapshot. Перед экспортом показывается
перечень включаемых данных; private keys и raw PII никогда не включаются.
