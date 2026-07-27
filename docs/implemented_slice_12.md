# Реализованный Slice 12: pilot hardening baseline

Дата проверки: 2026-07-21.

Статус: инженерный контур реализован. Независимая и полевая приёмка не
завершена; реальные хозяйственные операции по-прежнему запрещены.

## Наблюдаемость

Backend публикует защищённые read-only endpoints:

- `GET /api/v1/operations/snapshot` для `COOPERATIVE_ADMIN`,
  `SECURITY_ADMIN` и `AUDITOR`;
- `GET /api/v1/operations/metrics` в Prometheus text format для тех же ролей.

Snapshot собирается одним read-only запросом и не содержит PII. Он фиксирует
schema revision, signed journal, outbox, активные сессии, открытые дела и
апелляции, federation conflicts/incidents/key rotations/offline epochs/forms,
crisis mandates и crisis forms.

HTTP-метрики используют bounded labels: route template, method и status class.
Произвольный URL, user id, member id, товар, текст ошибки и персональные данные
в labels не попадают. Structured JSON log теперь всегда содержит release и
node id вместе с request id, route, duration и result code.

`coopctl diagnostics` выдаёт тот же PII-free snapshot локальному оператору.
GUI получил read-only раздел `Эксплуатация` с автоматическим обновлением раз в
30 секунд. Команд изменения хозяйственного состояния на этом экране нет.

## Capacity smoke

Read-only runner `cooperative_clearing.tools.capacity` и обёртки
`scripts/capacity-smoke.sh` / `scripts/capacity-smoke.ps1` разрешают только
`/health/live` и `/api/v1/system/status`. Отчёт содержит duration, RPS,
success/error, p50/p95/p99/max и проверяемые пороги.

Проверка текущего Docker Desktop host:

```text
requests=500 concurrency=20 successes=500 errors=0
rps=424.258 p95_ms=51.839 threshold_p95_ms=250 passed=true
```

Это smoke реализации, а не доказательство ёмкости минимального production host.
Форма полевого отчёта находится в `evidence_templates/capacity_report.md`.

## Accessibility baseline

Self-contained DOM audit проверяет duplicate ids, form labels, accessible names
кнопок и ссылок, image alt, heading order и positive tabindex. Он применён к
входу и обязательной смене bootstrap-пароля. Экран эксплуатации имеет отдельный
component test. DOM baseline не заменяет ручную клавиатурную проверку,
screen reader, contrast/reflow и browser/device matrix.

## Production evidence

`scripts/collect-production-evidence.sh` и PowerShell-вариант создают локальный
пакет без operational logs, secrets и raw PII. В него входят:

- manifest с явными privacy flags;
- commit SHA и dirty state;
- stack и image inventory;
- live/ready/system health;
- operational diagnostics;
- signed journal verification;
- OpenAPI SHA-256;
- `COMPLETE` и `SHA256SUMS`.

В каноническом `production` грязное Git-дерево запрещено без override (уточнено в Slice 28). Каталог `evidence/`
игнорируется Git. Контрольный пакет `release-20260721T204307Z` успешно прошёл
повторную проверку всех checksum; он является локальным runtime-артефактом и
не включён в репозиторий.

Шаблоны независимых решений находятся в `docs/evidence_templates/`:
security review, legal review, capacity, accessibility/browser matrix,
ежемесячный pilot report и production readiness decision.

## Контракты и проверки

- schema revision не менялась: `0014_federation_paper_forms`;
- OpenAPI: 228 paths;
- backend/frontend OpenAPI SHA-256:
  `eefcab76fb459267bb22bdeca83c66ca3e4b3f04da4ba9cef53134a2a6b0031a`;
- Ruff: PASS;
- mypy: 199 source files, PASS;
- backend: 129 tests, PASS;
- backend coverage: 78.41%, порог 75%;
- frontend: 43 test files / 103 tests, PASS;
- frontend coverage: 83.08% statements, 70.26% branches, 77.10% functions,
  89.35% lines;
- frontend typecheck и production PWA build: PASS;
- PowerShell parser и Bash syntax: PASS;
- deployed `verify-stack.ps1`: PASS на `http://127.0.0.1:8080`;
- deployed journal: 230 events, sequence 230, failures отсутствуют.

Coverage проверен в отдельном Compose project с чистой PostgreSQL volume,
поскольку повторное использование seed DB корректно уводит demo loaders в
идемпотентную ветвь и искажает coverage. Временный project и volume удалены.

## Незакрытые внешние критерии

Slice 12 нельзя считать production approval до выполнения и подписания:

1. capacity report на минимальном целевом Linux host и production-like dataset;
2. ручной browser/device/accessibility matrix;
3. независимый security review с повторной проверкой исправлений;
4. юридическое заключение по выбранной юрисдикции и подписанным policies;
5. FULL backup/restore с recovery custodians и измеренными RPO/RTO;
6. шестимесячный ограниченный пилот с ежемесячными решениями и stop conditions;
7. итоговый подписанный production readiness decision.

Ни unit-тест, ни локальный smoke не могут закрыть эти пункты автоматически.
