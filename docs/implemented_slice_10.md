# Реализованный Slice 10: резервы и ограниченный кризисный режим

Статус: реализовано и проверено на Linux-контейнерах с PostgreSQL. Срез добавляет
проверяемые нормативы критических резервов, временный кризисный мандат, точное
нормирование без создания долга и нумерованный бумажный контур.

Пороговые значения демонабора не являются policy пилота. Активация механизма в
реальной организации требует закрытия применимых open decisions, утверждения
оснований, ролей, сроков review, правил исключений и формы итогового отчёта.

## Границы власти

- кризисный режим является ограниченным мандатом, а не административным superuser;
- proposal и activation выполняют разные люди с активными scoped roles;
- мандат содержит evidence, scope, capabilities, exit criteria, safe state,
  обязательный review, expiry и абсолютный maximum end;
- maximum duration ограничена 90 днями, скрытое продление отсутствует;
- истёкший мандат немедленно считается `EXPIRED` при проверке полномочия, даже если
  фоновая материализация статуса ещё не выполнялась;
- закрытие или expiry разрешены только после reconciliation незавершённых выдач и
  paper forms и создают immutable report;
- кризис не списывает паи, не создаёт обязательства, не меняет reputation и не
  отменяет appeal.

Поддержаны отдельные capabilities: ограничения новых прав и federation,
усиленные evidence/audit, offline epoch, приоритет критических ресурсов,
нормирование, paper forms и защитная заморозка. Реализованные команды этого
среза используют только `ENABLE_RATIONING`, `ENABLE_PAPER_FORMS` и
`ENHANCED_AUDIT`; остальные значения являются типизированным контрактом для
следующих enforcement-срезов.

## Резервы

`ReserveTarget` хранит ресурс, единицу, целевой объём, критический минимум,
пороги coverage days, допустимый возраст снимка, policy version и hash условий.
Новая версия проходит обычный proposal и независимый approval. Утверждение
атомарно переводит прежнюю версию в `RETIRED`; ротация запрещена, пока старый
норматив используется активным правилом действующего мандата.

`ReserveSnapshot` является append-only физическим наблюдением с READY evidence:

```text
available = physical_verified_quantity - committed_quantity
coverage_days = available / consumption_rate_per_day
```

Непроверенный остаток не вводится в snapshot. Отклонённое качество не может
увеличить verified amount, committed не может превышать verified, confidence
ниже `0.5` даёт `UNKNOWN`. `NORMAL`, `WARNING` и `CRITICAL` вычисляются чистой
Decimal-функцией по количеству, coverage, quality и policy thresholds.

## Нормирование

Правило фиксирует target, eligible policy, protected minimum, maximum per member,
период, формулу и immutable terms hash. Proposal и approval разделены. Новая
версия подписанно выводит прежнюю в `RETIRED`, только если у неё нет
`PROPOSED`/`RESERVED` allocations.

Расчёт детерминирован и использует точность `10^-12`:

1. каждому eligible участнику резервируется protected minimum, если общего
   доступного остатка достаточно;
2. если остатка меньше суммы минимумов, он делится поровну без скрытого приоритета;
3. остаток распределяется поровну либо по целочисленным весам;
4. maximum per member ограничивает каждую долю, неделимый квант остаётся
   нераспределённым;
5. вход, snapshot, rule hash и allocations замораживаются в preview;
6. независимый confirm повторно проверяет свежий snapshot и совокупный резерв под
   cooperative advisory lock.

Конкурентный integration test создаёт два preview по `30 KG` при фактически
доступных `45 KG`. Ровно один confirm проходит, второй получает
`RATIONING_INPUT_STALE`. Отмена освобождает все невыданные назначения. Выдача
требует отдельного evidence, не может быть выполнена получателем или confirmer и
явно содержит `creates_debt=false`.

## Paper forms

Форма имеет уникальный номер в кооперативе, checksum, тип, назначенного человека,
мандат и expiry. Issue и последующий record выполняют разные люди; повтор номера,
checksum mismatch, ввод после срока и повторный ввод отклоняются. Payload получает
canonical SHA-256, а запись и итоговый report защищены append-only trigger.

## Роли и рабочие очереди

- `CRISIS_OPERATOR` предлагает норматив, мандат и правило, строит preview, выдаёт
  ration и paper form;
- `CRISIS_CONTROLLER` независимо утверждает policy/мандат/plan, записывает
  physical snapshot и бумажную форму;
- `INVENTORY_CONTROLLER` может фиксировать физический snapshot;
- независимый `CRISIS_CONTROLLER` или `AUDITOR` выполняет review/close;
- controller workspace показывает все draft rules и previewed plans в доступном
  cooperative scope, а не только мандаты с наступившим review.

Private allocation и paper-form read разрешены самому участнику либо scoped staff.
Точный запас не публикуется во federation-каталог автоматически.

## Хранение и миграция

Revision `0012_crisis_reserves` добавляет в schema `solidarity`:

- `reserve_targets`, `reserve_snapshots`;
- `crisis_mandates`, `crisis_reviews`, `crisis_reports`;
- `rationing_rules`, `rationing_plans`, `rationing_allocations`, `ration_issuances`;
- `crisis_paper_forms`.

CHECK/FK/unique constraints ограничивают состояния, периоды, quantities, hashes и
actor/event references. Snapshot, review, issuance и report append-only для
runtime-role; DELETE запрещён для всего контура. Downgrade ниже `0012` отклоняется,
если существуют кризисные записи или назначения ролей. На пустой БД проверен цикл
`0011 -> 0012 -> 0011 -> 0012`; Alembic drift отсутствует.

## API и GUI

`/api/v1/crisis` публикует 23 paths: targets/snapshots, mandates/reviews,
rules/plans/allocations/issuances, paper forms, reports и role workspaces. Все
изменяющие команды требуют `Idempotency-Key`; переход существующего объекта также
проверяет version и, где требуется, canonical hash.

OpenAPI содержит 176 paths. `backend/openapi.json` и `frontend/openapi.json`
имеют одинаковый SHA-256
`45CCC386E00364046861BEC2F349217B32682C8D7F25865D6193E3555FA3A45E`.

Рабочее место «Резервы и кризис» содержит вкладки «Резервы», «Мандаты»,
«Нормирование», «Бумага» и «Отчёты». В нём доступны evidence upload, dual-control
очереди, preview/confirm/cancel, выдача, review/close и immutable report. Команды
скрываются по роли, но backend повторно проверяет scope и разделение людей.

## Демоданные

Идемпотентное учение `DEMO-CRISIS-001` проходит production lifecycle:

1. утверждается норматив `CABBAGE` на `100 KG`;
2. независимый physical snapshot подтверждает `50 KG`, расход `10 KG/day` и
   состояние `WARNING` с coverage `5 days`;
3. два человека предлагают и активируют мандат платёжного сбоя;
4. утверждается equal rule с protected minimum `2 KG` и maximum `5 KG`;
5. Нине создаётся, подтверждается и выдаётся `5 KG` с READY evidence без долга;
6. бумажная форма `DEMO-PAPER-001` выдаётся и независимо вводится;
7. отдельный reviewer фиксирует review и закрывает мандат после reconciliation;
8. immutable report сохраняет counts, responsibility snapshot и corrective actions.

Повторный seed находит report и не создаёт новые записи, evidence или signed events.

## Проверка

- backend: Ruff по репозиторию и strict mypy по 172 source files;
- backend: 104 Pytest на чистой PostgreSQL test DB, coverage 79,80% при пороге 75%;
- frontend: strict TypeScript, production PWA build, 31 test files и 85 Vitest;
- frontend coverage: 84,24% statements, 70,59% branches, 78,60% functions,
  89,09% lines;
- migration: clean install, empty downgrade/upgrade, populated downgrade guard и
  `alembic check` без drift;
- OpenAPI: 176 paths, из них 23 paths кризисного контура;
- concurrency: один verified stock нельзя подтвердить двумя competing plans;
- isolation: кризисные события не создают obligation, share contribution,
  exposure commitment или reputation event;
- append-only: прямое изменение reserve snapshot отклонено PostgreSQL.
## Проверка production-like Docker-стенда

- runtime-образы `api` и `frontend` собраны их production Dockerfile без локальных bind mounts;
- `docker compose --profile demo up -d --remove-orphans` применил миграцию
  `0011_solidarity_aid -> 0012_crisis_reserves`; API, worker, frontend, gateway и PostgreSQL
  перешли в healthy;
- `/health/live` вернул `LIVE`, `/health/ready` вернул `READY` с `database`, `blob_store`
  и `node_key` в состоянии `UP`;
- `coopctl verify-journal` проверил 203 события до sequence 203, failures отсутствуют,
  последний hash: `sha256:04eb1e335a08d65d111a251b772a35cfac54e2f9ce844d3114254747e928c18c`;
- повторный `seed-demo` не изменил число событий, sequence и последний hash;
- browser smoke на `1440x900` и `375x844` открыл рабочее место, получил reserve target и
  snapshot, отрисовал одну строку таблицы без document-level horizontal overflow;
- browser console на desktop/mobile не содержит warning или error.
