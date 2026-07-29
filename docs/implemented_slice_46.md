# Реализованный Slice 46: локальная наблюдаемость без Интернета

Дата проверки: 2026-07-29.

Статус: code-level Acceptance 131 закрыт воспроизводимым Docker-сценарием.
Журналы, защищённые метрики и health-checks доступны локальному оператору без
внешнего SaaS, DNS или канала телеметрии.

## Эксплуатационный контракт

Обычный узел публикует портал только на loopback-адресе оператора, например
`http://127.0.0.1:8080`. API, worker, frontend и PostgreSQL не получают
отдельных внешних портов.

Acceptance-override `compose.observability-test.yaml` делает все четыре сети
`edge`, `app`, `web` и `data` внутренними. В такой топологии Docker Desktop
намеренно не маршрутизирует опубликованный порт с host. Поэтому проверку
выполняет одноразовый read-only контейнер `observability-probe` в той же
локальной `edge`-сети. Имя `gateway` добавляется в trusted-host allowlist только
тестового API; основной Compose-контракт не расширяется.

Probe проверяет:

1. `/health/live` возвращает `LIVE`, а `/health/ready` возвращает `READY`;
2. локальный оператор проходит штатную аутентификацию;
3. временный bootstrap-пароль меняется через штатный API и не сохраняется;
4. защищённые snapshot, host readiness и Prometheus metrics доступны роли;
5. присутствуют `coop_build_info`, `coop_http_requests_total`,
   `coop_operational_records` и `coop_host_check_severity`;
6. `edge`, `app`, `web` и `data` имеют Docker-признак `Internal=true`;
7. gateway не достигает адреса TEST-NET `198.51.100.1`;
8. bounded local logs содержат API и gateway и не содержат исходный или новый
   пароль оператора;
9. отчёт не содержит access token, пароль или сырое тело метрик;
10. экспорт телеметрии отмечен как `DISABLED`.

## Запуск

Linux:

```bash
bash ./scripts/test-local-observability.sh
```

Windows PowerShell:

```powershell
.\scripts\test-local-observability.ps1
```

Сценарий использует отдельные project name, port и volumes, а затем удаляет
тестовый стек. Для повторного использования уже собранных локальных образов
задайте `COOP_OBSERVABILITY_SKIP_BUILD=1` в Linux или передайте `-SkipBuild` в
PowerShell. Основной пользовательский узел сценарий не останавливает.

## Доказательство

Каждый прогон создаёт локальный каталог `evidence/local-observability-<UTC>`:

- `report.json` формата `cooperative-clearing-local-observability-v1`;
- `network-isolation.json` с четырьмя internal-сетями и blocked egress;
- bounded `runtime.log` только для локального анализа;
- переносимый LF-файл `SHA256SUMS`.

На 2026-07-29 независимые PowerShell и Bash прогоны завершились `PASSED` на
schema `0039_participant_address_events`; оба checksum-набора прошли
`sha256sum -c`. Unit-набор probe проверяет внешний origin, неполную изоляцию,
отсутствующую metric family, многострочный secret и утечку исходного либо
сменённого пароля.

## Границы

Runtime logs намеренно не входят в экспортируемый diagnostic bundle: локальная
доступность не означает разрешение передавать их третьей стороне. Retention,
rotation и физический доступ утверждаются кооперативом.

Этот срез не доказывает работу конкретного ИБП, диска, часов, сертификатов или
backup на целевом Linux-сервере. Назначение операторов, target-host evidence,
privacy/security review, бумажный runbook и полевое учение остаются открытыми
production-gates.
