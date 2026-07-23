# Реализованный Slice 7: локальный клиринг

Статус: реализовано и проверено на Linux-контейнерах с PostgreSQL. Срез
добавляет детерминированный локальный взаимозачёт подтверждённых обязательств,
неизменяемый вход, независимое подтверждение результата, доказательный спор,
атомарную финализацию и воспроизводимый proof.

Клиринг не создаёт деньги и не подтверждает физическое исполнение. Он отдельно
увеличивает `quantity_cleared`; `quantity_fulfilled` меняется только через
приёмку исполнения. Реальные единицы оценки, корзина и пороги dual control не
определены кодом: `OD-003`, `OD-004` и `OD-043` остаются открытыми до пилота.

## Детерминированный расчёт

Чистый engine `LOCAL_NETTING` версии `1.0.0` работает только с `Decimal`,
фиксированной scale и явным rounding mode. Он:

- сортирует вход по стабильному составному ключу и не зависит от порядка DTO;
- сначала закрывает встречные двусторонние дуги, затем простые циклы;
- ограничивает iterations и длину цикла утверждённой policy;
- исключает спорные, запрещённые, просроченные по eligibility и собственные
  обязательства со стабильным reason code;
- не превышает остаток обязательства и его risk limit;
- сохраняет баланс зачёта по каждому участнику и единице;
- формирует `input_hash`, `parameters_hash` и `result_hash` из canonical JSON.

Golden vectors покрывают двусторонний зачёт, трёхсторонний цикл, округление,
risk limit, исключения, conservation и permutation invariance. Независимый
verifier пересчитывает результат из proof и отклоняет подмену любого hash.

## Жизненный цикл

Цикл проходит состояния:

```text
DRAFT -> COLLECTING -> INPUT_FROZEN -> PREVIEWED
      -> DISPUTE_WINDOW -> READY_TO_FINALIZE
      -> FINALIZED -> RECONCILED
```

Открытый спор временно переводит цикл в `DISPUTED`. Независимое решение либо
возвращает его в окно возражений, либо требует нового preview. Переходы
проверяют `expected_version`; повтор команды защищён `Idempotency-Key`.

При freeze сохраняются версии и значения всех eligible obligations. Preview
связан с точным input/policy hash. Контролёр подтверждает именно эту пару
hashes. Финализатор повторно блокирует цикл и обязательства, сверяет frozen
версии, применяет entry ровно один раз, создаёт signed events и proof в одной
транзакции. Два конкурентных финализатора дают один commit и один
`VERSION_CONFLICT`.

Reconcile формирует участнические statements и один idempotent accounting
export draft. Экспорт содержит ordered source references и package hash, но не
считает клиринговую единицу законным платёжным средством и не задаёт проводки.

## Роли и независимость

- `CLEARING_OPERATOR`: предлагает policy, создаёт цикл, collect/freeze/preview;
- `CLEARING_CONTROLLER`: независимо утверждает policy и preview, решает спор;
- `CLEARING_FINALIZER`: закрывает окно, финализирует и сверяет цикл;
- сторона entry: видит свои entries/positions/statements и открывает спор с
  READY evidence;
- `AUDITOR` и `SECURITY_ADMIN`: читают полный локальный контур и проверяют proof.

Автор policy не может её утвердить. Инициатор preview не выполняет независимое
подтверждение. Решающий спор не может быть его заявителем или стороной
затронутого обязательства. Backend повторно проверяет active member,
membership и role assignment для каждой команды.

GUI «Клиринг» показывает циклы и этапы, hashes, обязательства до/зачтено/после,
позиции, подтверждения, споры и evidence, verifier, statements и accounting
package. Команды видны по роли, но полномочия окончательно проверяет API.

## Хранение и миграция

Revision `0009_local_clearing` создаёт:

- `exchange.clearing_policies`;
- `exchange.clearing_cycles`;
- `exchange.clearing_input_snapshots`;
- `exchange.clearing_entries`;
- `exchange.clearing_positions`;
- `exchange.clearing_approvals`;
- `exchange.clearing_disputes`;
- `exchange.clearing_proofs`;
- `exchange.clearing_statements`;
- `exchange.clearing_accounting_exports`.

В `exchange.obligations` добавлены `quantity_cleared` и
`clearing_allowed`. CHECK запрещает сумме fulfilled и cleared превышать total.
Snapshots, entries, approvals, proofs, statements и exports имеют append-only
DB guards и урезанные runtime grants. Downgrade ниже `0009` останавливается,
если будут потеряны клиринговые данные, зачтённые количества или роли.

## API

Чтение:

```text
GET /api/v1/clearing/policies
GET /api/v1/clearing/cycles
GET /api/v1/clearing/cycles/{id}/input
GET /api/v1/clearing/cycles/{id}/entries
GET /api/v1/clearing/cycles/{id}/positions
GET /api/v1/clearing/cycles/{id}/approvals
GET /api/v1/clearing/cycles/{id}/disputes
GET /api/v1/clearing/cycles/{id}/proof
GET /api/v1/clearing/cycles/{id}/statements/{member_id}
GET /api/v1/clearing/cycles/{id}/accounting-export
```

Команды:

```text
POST /api/v1/clearing/proofs/verify
POST /api/v1/clearing/policies
POST /api/v1/clearing/policies/{id}/approval
POST /api/v1/clearing/cycles
POST /api/v1/clearing/cycles/{id}/collect
POST /api/v1/clearing/cycles/{id}/freeze-input
POST /api/v1/clearing/cycles/{id}/preview
POST /api/v1/clearing/cycles/{id}/approvals
POST /api/v1/clearing/cycles/{id}/disputes
POST /api/v1/clearing/disputes/{id}/decision
POST /api/v1/clearing/cycles/{id}/ready
POST /api/v1/clearing/cycles/{id}/finalize
POST /api/v1/clearing/cycles/{id}/reconcile
```

## Демоданные

Идемпотентный seed создаёт active policy `LOCAL_NETTING/1.0.0`, недельный цикл
`DEMO-WEEK-2035-01`, встречные обязательства Анны и Павла, независимое
подтверждение Елены, финализацию Павлом, proof, две statements и accounting
export. Итоговый цикл `RECONCILED`; физически исполненное количество при этом
не меняется.

Демонстрационные scale, rounding, minimum operation, approvals и dispute
window служат только для разработки. Их нельзя переносить в pilot/production
без закрытия открытых решений и утверждённой policy.

## Проверка

- backend: Ruff, strict mypy по 136 source files и 76 Pytest на PostgreSQL;
- backend coverage: 80.03% при обязательном пороге 75%;
- frontend: strict TypeScript, production PWA build и 68 Vitest;
- frontend coverage: 89.55% statements, 72.43% branches, 85.86% functions и
  93.35% lines;
- миграция: schema head `0009_local_clearing`, повторный upgrade и
  destructive downgrade guard на заполненной БД;
- конкуренция: два finalizer одновременно создают один proof и применяют
  точные preview amounts один раз;
- безопасность: append-only DB test, participant visibility, role separation,
  evidence-backed dispute и независимое решение;
- демонабор повторно выполняется без дубликатов и сохраняет journal chain.

Production-like Compose runtime также проверен после сборки images: миграция,
инициализация узла, bootstrap identity и повторяемый demo seed завершились с
кодом `0`; API, frontend, worker, gateway и PostgreSQL вышли в healthy state.
`verify-stack.ps1` подтвердил readiness, `/api/v1/system/status` вернул
`OPERATIONAL`, schema `0009_local_clearing` и worker `RUNNING`. В runtime-БД
цикл `DEMO-WEEK-2035-01` имеет статус `RECONCILED`, версию `8`, два входа,
две записи зачёта на общую сумму `100`, один proof, две statements и один
accounting export; физическое исполнение обязательств осталось равным нулю.
Проверка подписанного журнала прошла без ошибок: `134/134` событий, failures
отсутствуют. В браузере Auditor увидел точные суммы `50 + 50`, проверил proof
как действительный; ошибок и предупреждений консоли не обнаружено.
