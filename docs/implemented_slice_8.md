# Реализованный Slice 8: споры и доверие

Статус: реализовано и проверено на Linux-контейнерах с PostgreSQL. Срез
добавляет общую процедуру рассмотрения дела, независимые решения и апелляции,
временные защитные меры, санкции, контекстные события надежности и
реабилитацию. Исправление не переписывает историю: отмененное решение получает
явную компенсирующую запись, а профиль пересчитывается из событий.

Численные шкалы в демополитике не утверждают правила пилота. `OD-025`,
`OD-029`, `OD-030`, `OD-031` и `OD-032` остаются открытыми.

## Процедура дела

Дело проходит состояния:

```text
OPEN -> RESPONSE_RECEIVED -> READY_FOR_DECISION -> DECIDED -> CLOSED
                                      |                |
                                      +---- REMANDED <-+
```

Открытие требует активной policy, проверяемого источника и evidence. Ответ
стороны хранится отдельно от исходного утверждения. `AUDITOR` подтверждает
готовность материалов, а `ARBITRATOR` перед решением обязан зарегистрировать
conflict declaration. Арбитр с конфликтом, сторона дела или автор исходного
решения не может принять соответствующее независимое решение.

Решение содержит stage, outcome, стандарт доказанности, fault class, causal
findings, установленный ущерб, мотивировку, последствия, evidence, снимок
состава и policy version. Решения, conflict declarations и reputation events
append-only на уровне PostgreSQL.

## Меры, санкции и исполнение ограничений

Protective measure предотвращает новый риск и не является выводом о вине. Она
имеет точный scope, основание, начало, expiry и review date. Поддержаны
дополнительная проверка, ограничение scope, приостановка роли/ключа и запрет
новых гарантий. Активный scope проверяется также в bounded-risk и identity
services: запрещенные commitment, guarantee и role assignment нельзя обойти
прямым вызовом другого API.

Sanction создается только из оригинального решения. До окончания appeal window
она остается `PENDING_APPEAL`; финализация и отзыв являются отдельными
событиями. Автоматическое движение паев или взыскание Slice 8 не выполняет.

## Апелляция и коррекция

Апелляция связывается с оригинальным решением и, при наличии, санкцией. Ее
рассматривает другой арбитр после отдельной проверки конфликта. Результат может
быть `AFFIRMED`, `MODIFIED`, `OVERTURNED` или `REMANDED`.

При отмене система:

- отзывает временную меру и санкцию отдельными signed events;
- переводит исходный reputation event в спорное состояние без удаления;
- добавляет `CORRECTION` со ссылкой `corrects_event_id`;
- отменяет несовместимый активный rehabilitation plan;
- закрывает дело, сохраняя оба решения и всю причинную цепочку.

## Контекстная надежность

Профиль строится чистой проекцией атомарных событий по контекстам. UI показывает
исполнения, нарушения, добросовестные самоисправления, спорные и отмененные
события, коррекции, достаточность выборки и source references. Единого score
человека нет. Вклад в фонд, размер пая, отказ от жалобы и защищенные признаки
не входят в модель.

Rehabilitation plan содержит проверяемые steps, evidence и критерий закрытия.
Завершение создает отдельное событие восстановления, но не удаляет прошлое.

## Роли и интерфейс

- участник открывает дело, отвечает и подает апелляцию в своем контуре;
- `AUDITOR` проверяет материалы, временные меры и спорные проекции;
- `ARBITRATOR` принимает независимые решения, рассматривает апелляции и
  закрывает реабилитацию;
- `RISK_ADMIN` может применить и снять ограниченную защитную меру;
- `COOPERATIVE_ADMIN` предлагает процедуру, а глобальный `AUDITOR` независимо
  утверждает ее;
- `SECURITY_ADMIN` и глобальный `AUDITOR` имеют полный read-only обзор.

Рабочее место «Споры» содержит разделы «Дела», «Апелляции», «Меры»,
«Репутация» и «Реабилитация», а также очереди аудитора и арбитра. Команды
скрываются по роли, но authorization, active membership, conflict rules,
expected version и idempotency повторно проверяются API.

## Хранение и миграция

Revision `0010_trust_procedural_fairness` создает в schema `trust`:

- `trust_policies`, `cases`, `conflict_declarations`;
- `protective_measures`, `arbitration_decisions`, `sanctions`, `appeals`;
- `reputation_events`, `rehabilitation_plans`, `rehabilitation_steps`.

Ограничения PostgreSQL проверяют допустимые состояния, сроки, bounded severity
и confidence, causal correction и единственность decision round. Runtime role
не может изменять immutable records. Downgrade ниже `0010` останавливается до
потери данных, если есть trust records или роль `ARBITRATOR`.

## API

`/api/v1/trust` публикует 27 paths: policy proposal/approval, scoped list и
detail дел, response/ready, conflicts, decisions, protective measures,
sanctions, appeals, reputation events/profile, rehabilitation plans/steps и
рабочие очереди аудитора/арбитра. Все 128 paths актуального OpenAPI собраны из
backend; файлы `backend/openapi.json` и `frontend/openapi.json` имеют одинаковый
SHA-256 `ACF9BC4A4EB8BAFEB1A2DF547FF7B2D8556B1994C736A8B67336E4F4993CD4BC`.

Изменяющие команды требуют `Idempotency-Key`; переход существующего агрегата
также требует `expected_version`. Evidence принимается только после завершения
content-addressed upload и проверки состояния `READY`.

## Демоданные

Идемпотентный сценарий `DEMO-TRUST-APPEAL-001` моделирует ошибочную
интерпретацию часового пояса. Он создает оригинальное решение
`SUBSTANTIATED`, временную меру, предупреждение и спорный `BREACH`, после чего
независимая апелляция `OVERTURNED` отзывает последствия и добавляет явную
`CORRECTION`. Итог: дело `CLOSED`, мера и санкция `REVOKED`, rehabilitation
`CANCELLED`; исходный факт остается `DISPUTED/PENDING`.

Два повторных запуска seed сохранили одно дело, одну апелляцию, два
reputation events и неизменную длину журнала.

## Проверка

- backend: Ruff, strict mypy по 149 source files, Alembic check и 82 Pytest;
- backend coverage: 80.52% при обязательном пороге 75%;
- frontend: strict TypeScript, production PWA build и 72 Vitest;
- frontend coverage: 85.47% statements, 71.21% branches, 79.92% functions и
  90.01% lines;
- clean migration: `0001 -> ... -> 0010`, повторный upgrade без операций;
- destructive downgrade на заполненной test DB отклонен, revision и дело
  сохранены;
- integration: полный overturned appeal, cross-module enforcement, scoped API,
  append-only trigger и auditor/arbitrator workspaces;
- production-like Compose: PostgreSQL, API, worker, frontend и gateway healthy;
- signed journal: `164/164` событий, sequence/hash/signature failures нет;
- runtime OpenAPI: 128 paths, включая 27 trust paths;
- browser: все пять разделов проверены на 1265x720 и 390x844, горизонтального
  overflow и console warnings/errors нет.

Фактический runtime-профиль Анны показывает спорный `BREACH` и действующую
`CORRECTION`, которая причинно ссылается на исходную запись. Это подтверждает
критерий среза: ошибочная мера проходит апелляцию без удаления истории и
воспроизводимо перестраивает контекстный профиль.
