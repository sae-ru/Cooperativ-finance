# Отчёт по ёмкости узла

## Идентификация

- release и commit SHA:
- дата UTC:
- минимальный целевой host: CPU, RAM, storage, filesystem, network:
- ОС, kernel, Docker и Compose:
- dataset: участники, офферы, события, партии, узлы:
- фоновые задачи и состояние sync:

## Сценарии и пороги

| Сценарий | Запросы | Concurrency | Error rate max | p95 max | RPS min |
|---|---:|---:|---:|---:|---:|
| Read-only health smoke | | | | | |
| Поиск каталога | | | | | |
| Расчёт landed cost | | | | | |
| Clearing preview | | | | | |
| Offline package verify/apply | | | | | |

## Результат

Приложить JSON-вывод `scripts/capacity-smoke.sh`, графики ресурсов, журнал
ошибок без PII и ссылки на evidence pack. Smoke на машине разработчика не
заменяет прогон на минимальном целевом host.

- Итог: PASS / FAIL
- Bottleneck и запас:
- Следующий прогон:
- Подписи владельца эксплуатации и независимого контролёра:
