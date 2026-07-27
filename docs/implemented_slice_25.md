# Реализованный Slice 25: безопасное объединение дубликатов участников

## Назначение

Slice 25 закрывает code-level процедуру объединения двух подтверждённых карточек одного человека. Операция не удаляет источник и не переписывает подписанную историю: ошибочная карточка получает статус `MERGED` и неизменяемую ссылку `merged_into_member_id` на основную карточку.

Автоматическое объединение намеренно консервативно. Оно разрешено только для дубля, у которого нет хозяйственной истории, паевых счетов, обязательств, доставок, репутационных решений или других доменных ссылок. Такие связи требуют решения в своём модуле и нового merge case.

## Доменный процесс

1. Постоянный `MEMBER_REGISTRAR` или `DATA_STEWARD` выбирает source и survivor одного кооператива, передаёт версии обеих карточек и 1-10 безопасных evidence refs.
2. PostgreSQL под блокировкой проверяет версии, статусы, cooperative scope, логины, членства, совпадающие активные адреса, конфликтующие адреса забора/доставки по умолчанию и все внешние FK на `identity.members`.
3. При препятствиях создаётся терминальное дело `BLOCKED` с понятной сводкой; данные не меняются.
4. Чистое дело получает `PENDING_REVIEW` на 24 часа.
5. Другой персонально связанный постоянный `SECURITY_ADMIN` подтверждает решение через TOTP step-up.
6. В момент решения все проверки повторяются. Любая новая ссылка или изменение версии переводит дело в `BLOCKED`.
7. При одобрении identifiers, memberships, participant addresses и единственный login переносятся на survivor; source остаётся в реестре как `MERGED`.

Истёкшее дело отображается как `EXPIRED`. При новой заявке на тот же source предыдущее истёкшее дело закрывается подписанным событием, поэтому partial unique index не оставляет вечную блокировку.

## Хранение и миграция

Revision `0032_member_duplicate_merge`:

- добавляет `identity.members.merged_into_member_id` и статус `MERGED`;
- CHECK требует точного соответствия между статусом `MERGED` и merge link и запрещает self-link;
- создаёт `identity.member_merge_cases` с source/survivor versions, evidence refs, blocker summary, requester, decider, expiry и version;
- partial unique index допускает только одно действующее `PENDING_REVIEW` дело на source;
- DB CHECK запрещает одинаковых requester/decider и source/survivor;
- функция `identity.member_merge_external_blockers(uuid)` обнаруживает все single-column FK на `identity.members` через системный каталог PostgreSQL.

Динамическая проверка означает, что новая доменная таблица автоматически блокирует merge, даже если её ещё не добавили в application registry. Из разрешённого переноса исключены только identifiers, memberships, participant addresses и user; исторические import rows остаются как исходное свидетельство.

Downgrade разрешён только до появления merge history. После первого дела или карточки `MERGED` миграция fail-closed, чтобы оператор не потерял карту идентичностей.

## API

```text
GET  /api/v1/admin/member-merge-cases
POST /api/v1/admin/member-merge-cases
POST /api/v1/admin/member-merge-cases/{case_id}/decision
```

Команды используют `Idempotency-Key`, optimistic versions, cooperative scope и permanent-role checks. Decision требует server-side step-up. Ответ команды содержит case id и итоговый status, но не раскрывает PII или внутренние значения identifiers.

## События и аудит

Подписанный журнал фиксирует:

- `identity.duplicate_merge_requested`;
- `identity.duplicate_merge_blocked`;
- `identity.duplicate_merge_decided`;
- `identity.duplicate_merge_rejected`;
- `identity.duplicate_merge_expired`.

Decision payload содержит source, survivor, однозначный mapping и blocker summary. Evidence хранится только как безопасные references/hashes. Каждая команда имеет audit entry и idempotency record.

## Интерфейс

В **Администрирование -> Дубликаты** оператор видит форму «ошибочная карточка / правильная карточка», последствия, очередь дел и локализованные препятствия. Названия SQL-таблиц пользователю не показываются: связи сгруппированы как паи и ответственность, сделки и доставки, подписанная история, взаимопомощь, кризисные записи и другие понятные области.

Подтверждение открывает отдельный protected-action dialog. Собственное дело подтвердить нельзя. RU и EN берутся из симметричных XML-файлов `lang/ru.xml` и `lang/en.xml`; light/dark используют общую дизайн-систему.

## Демоданные и проверки

Demo seed создаёт чистый дубль `Anna Petrova (duplicate record)` и pending case для независимого решения `security`. Обычное дело живёт 24 часа; учебному делу даётся 30 дней, чтобы сценарий не исчезал между занятиями. Повторный seed не создаёт второй case и не возвращает уже объединённую карточку в активный статус.

Автоматические проверки покрывают:

- нормализацию и минимизацию evidence refs;
- clean merge с переносом identifier, membership и address;
- сохранение source и merge mapping;
- self-review rejection, TOTP и independent approval;
- блокировку двух логинов и двух адресов забора/доставки по умолчанию;
- обнаружение подписанной истории динамической FK-функцией;
- idempotency replay и signed event set;
- RU GUI создания, blocker explanation без SQL constants и protected decision.

## Финальный checkpoint

Проверено на полном локальном Docker-стенде: backend — `215 passed, 1 deselected`; frontend — `62` test files и `173` tests, coverage `82.04%` statements / `75.22%` functions / `88.14%` lines; TypeScript typecheck и production PWA build; Ruff без замечаний; strict mypy по `208` source files; RU/EN XML — по `846` совпадающих message keys и `396` совпадающих system values.

Миграция прошла цикл `0031 -> 0032 -> 0031 -> 0032` и `alembic check`. Живой узел имеет статус `OPERATIONAL`, revision `0032_member_duplicate_merge`, работающий worker и единственный опубликованный gateway `8080`. В браузере проверены демо-case, RU/EN, light/dark и отсутствие console errors. Решение демо-case не выполнялось, чтобы сохранить повторяемый учебный сценарий; реальное одобрение с TOTP проверено интеграционным тестом PostgreSQL.

## Ограничения

Code-level срез не разрешает автоматически:

- объединять карточки разных кооперативов;
- переносить паи, долги, поручительства, сделки, доставки, санкции или репутацию;
- решать наследование, смерть, недееспособность и правопреемство;
- отменять обязательный независимый юридический и security review процедуры.

Для этих случаев нужны отдельные доменные transfer/succession workflows и внешне утверждённые регламенты. До их появления система выбирает остановку с объяснением вместо рискованного переписывания истории.