# Реализованный Slice 5: сделки и обязательства

Статус: реализовано и проверено на Linux-контейнерах с PostgreSQL. Срез
создаёт локальную сделку, доказуемую цепочку исполнения и персональную
ответственность сторон и перевозчика. Он не выполняет денежную оценку, не
резервирует паи и не проводит клиринг.

## Сделка и точные условия

Администратор кооператива предлагает сделку как версионированный набор
обязательств. Канонический JSON условий получает hash вида
`sha256:<64 lowercase hex>`. Для каждой версии отдельно сохраняются:

- неизменяемый payload условий и его hash;
- полный набор сторон;
- личные подтверждения участников с ролью и signed event;
- обязательства, которые появляются только после последнего обязательного
  подтверждения.

Участник подтверждает одновременно `terms_version`, `terms_hash` и ожидаемую
версию сделки. Изменение условий создаёт новую версию, новый hash и новый
набор подтверждений. Подтверждение старой или подменённой версии невозможно.

## Исполнение и приёмка

Для каждого обязательства база и домен обеспечивают инвариант:

```text
0 <= quantity_submitted + quantity_fulfilled <= quantity_total
```

Должник предъявляет исполнение и тем самым резервирует его количество в
`quantity_submitted`. Кредитор отдельной командой принимает всё, часть или
ноль. Принятое количество переходит в `quantity_fulfilled`, а отклонённый
остаток освобождается для повторного исполнения. Запрет частичного исполнения,
обязательность evidence и optimistic version проверяются сервером.

Статусы обязательства: `ACTIVE`, `PARTIALLY_FULFILLED`, `FULFILLED`,
`OVERDUE`, `DISPUTED`, `DEFAULTED`, `CLOSED`. Просрочка устанавливается явной
аудируемой командой, использующей переданный момент времени, а не скрытым
фоновым изменением.

## Логистика

Заказ доставки проходит только последовательность:

```text
OFFERED -> ACCEPTED -> IN_TRANSIT -> DELIVERED
```

Администратор назначает именованного участника-перевозчика. Этот участник с
ролью `LOGISTICS_OPERATOR` лично принимает заказ и остаётся тем же
ответственным пользователем до доставки. Для погрузки и доставки обязательны
READY evidence. Исполнение может ссылаться на доставленный заказ, но доставка
сама по себе не означает приёмку товара кредитором.

## Спор и независимое решение

Должник или кредитор может открыть спор по обязательству либо конкретному
исполнению. Это атомарно замораживает затронутое исполнение, обязательство и
сделку. Решение принимает именованный `COOPERATIVE_ADMIN`, `RISK_ADMIN` или
`AUDITOR`, который не является заявителем, должником или кредитором.

Поддерживаются действия:

- `REJECT_CLAIM`;
- `CONTINUE_PERFORMANCE`;
- `DEFAULT_OBLIGATION`;
- `CLOSE_OBLIGATION`.

Решение требует evidence, пояснения и ожидаемую версию спора. Предыдущие
статусы сохраняются для безопасного продолжения или отклонения претензии.
Закрытый или дефолтный результат пересчитывает статус всей сделки. Полная
апелляция, санкции и репутационные проекции относятся к Slice 8.

## Роли и видимость

- `COOPERATIVE_ADMIN` предлагает и изменяет сделку, создаёт заказ доставки;
- каждая сторона лично подтверждает точную версию условий;
- только должник предъявляет исполнение;
- только кредитор принимает или отклоняет его;
- только назначенный `LOGISTICS_OPERATOR` ведёт заказ после принятия;
- просрочку отмечают `COOPERATIVE_ADMIN`, `RISK_ADMIN` или `AUDITOR`;
- спор разрешает независимый участник с одной из этих контрольных ролей;
- участники видят только свои сделки, перевозчик свои заказы, scoped admin
  свой кооператив, глобальный auditor/security весь локальный узел.

GUI скрывает команды, когда роль, сторона или состояние их не допускают, но
окончательное решение всегда принимает backend.

## Хранение и миграции

Revision `0006_exchange_vertical_flow` создаёт в схеме `exchange`:

- `deals`, `deal_terms_versions`, `deal_parties`, `deal_confirmations`;
- `obligations`, `fulfillments`, `acceptance_records`;
- `logistics_orders`, `obligation_disputes`.

Revision `0007_exchange_dispute_resolution` добавляет версионированное
разрешение спора, независимого решающего, выбранное действие и signed event.
Downgrade останавливается, если уже существуют разрешённые споры: потеря
юридически значимой истории не маскируется удалением колонок.

Все команды атомарно записывают хозяйственное состояние, signed event, audit,
outbox и idempotency result. События входят в локальную hash-chain и
подписываются Ed25519. Команда `coopctl verify-journal` независимо проверяет
последовательность, hashes, ключи и подписи и возвращает JSON и exit code.

## API

Чтение:

```text
GET /api/v1/exchange/deals
GET /api/v1/exchange/deals/{deal_id}
GET /api/v1/exchange/obligations
GET /api/v1/exchange/obligations/{obligation_id}/fulfillments
GET /api/v1/exchange/acceptances
GET /api/v1/exchange/logistics-orders
GET /api/v1/exchange/disputes
```

Команды:

```text
POST /api/v1/exchange/deals
PUT  /api/v1/exchange/deals/{deal_id}/terms
POST /api/v1/exchange/deals/{deal_id}/confirmations
POST /api/v1/exchange/obligations/{obligation_id}/fulfillments
POST /api/v1/exchange/fulfillments/{fulfillment_id}/acceptance
POST /api/v1/exchange/obligations/{obligation_id}/disputes
POST /api/v1/exchange/disputes/{dispute_id}/resolution
POST /api/v1/exchange/overdue-scans
POST /api/v1/exchange/obligations/{obligation_id}/logistics-orders
POST /api/v1/exchange/logistics-orders/{order_id}/{accept|pickup|deliver}
```

Каждая команда требует `Idempotency-Key`; изменяемые агрегаты требуют
ожидаемую версию. OpenAPI backend и frontend побайтно совпадают, а клиентские
TypeScript-типы сгенерированы из этого контракта.

## Интерфейс

PWA содержит рабочее место «Сделки» с реестром и подробностями условий,
сторон, подтверждений и обязательств. Ролевые панели позволяют предложить и
пересмотреть сделку, подтвердить условия, предъявить и принять исполнение,
вести доставку, открыть и независимо разрешить спор, а также запустить
контролируемую проверку просрочки. Таблицы и формы адаптированы для desktop и
mobile; действия исчезают для замороженного или завершённого состояния.

## Демоданные

Идемпотентный seed создаёт сделку `Demo cabbage delivery` между Анной и
Еленой на `20.00 KG` капусты. Обе стороны подтверждают одинаковый hash
условий. Елена принимает ответственность перевозчика, прикладывает акты
погрузки и доставки `8.00 KG`; Анна предъявляет исполнение, а Елена принимает
`6.00 KG` и отклоняет `2.00 KG` для замены.

После повторного seed в runtime DB остаются одна сделка, одно обязательство,
один заказ доставки и те же signed events. Текущая демосделка намеренно не
содержит открытого спора: спор и все варианты решения покрыты integration- и
GUI-тестами.

## Проверка

- backend: Ruff, strict mypy по 113 файлам и 58 Pytest на PostgreSQL;
- backend coverage: 80.37% при пороге 75%;
- frontend: strict TypeScript, 46 Vitest, 89.89% statements, 71.73% branches,
  87.09% functions и 92.67% lines;
- migration: чистое `0001 -> 0007`, `0007 -> 0006 -> 0007`, downgrade guard
  на разрешённом споре и `alembic check`;
- integration: версии условий, подтверждения сторон, partial/zero acceptance,
  просрочка, спор, независимое решение, логистика, idempotency и конкурентные
  команды;
- Docker runtime: API, frontend, gateway, PostgreSQL и worker healthy;
- journal runtime: 79 из 79 событий проверены, failures отсутствуют;
- frontend build: production PWA и service worker собраны.

## Что ещё не готово

- паевые резервы, гарантии, aggregate exposure и bounded liability Slice 6;
- клиринговые циклы и proofs Slice 7;
- полные апелляции, санкции и репутация Slice 8;
- солидарность, кризисный режим, federation и межузловой клиринг следующих
  срезов;
- backup/restore drill, production TLS и подписанный offline release bundle из
  общих критериев production readiness.

Следующий production slice: Shares and bounded risk.
