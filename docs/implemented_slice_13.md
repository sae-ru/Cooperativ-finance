# Реализованный Slice 13: федеративный поиск, логистика и резервы

Дата проверки: 2026-07-21.

Статус: инженерный Slice 13 реализован и проверен. Контур разрешает искать
предложения доверенных узлов, сравнивать полную стоимость и выполнять
подписанную сагу резервирования товара и логистики. Это не является
межузловым клирингом: сертификат commit, применение клиринговых позиций и
reconciliation нескольких независимых БД относятся к Slice 14.

## Каталог и поиск

Реализованы versioned `FederatedOffer`, `OfferIndexSnapshot` и
`LogisticsQuote`. Для количества и стоимости используется `Decimal` и точный
масштаб единицы; float в хозяйственном расчёте отсутствует. Предложение хранит
home node, продавца, товар, качество, сертификаты, доступность, минимальную
партию, цену, обязательный сбор, сроки, policy versions и подписанный hash.

Поддержаны режимы:

- `DIRECT`: bounded fan-out к выбранным доверенным узлам;
- `INDEXED`: подписанный индекс и импорт проверенных результатов;
- `CACHED_OFFLINE`: локальный подписанный кэш без утверждения, что источник
  доступен сейчас.

Поиск проверяет trust contract, capability, certificate fingerprint,
protocol, подпись, home node, sequence, expiry и допустимый возраст. Результат
содержит статус каждого peer; отказ одного узла не превращает ответы остальных
в ошибку. Ранжирование `LANDED_COST_V1` детерминированно сравнивает стоимость
товара, обязательные сборы и подтверждённые компоненты доставки. Оценочная
логистика остаётся явно помеченной и не смешивается с подтверждённой.

## Онлайн-протокол узлов

Единственная межузловая точка входа:

```text
POST /api/v1/federation/peer/messages
```

Протокол `CC-PEER-1` использует canonical JSON, Ed25519, `message_id`, точные
source/target node codes, operation, capability, payload hash, issued/expiry
окно и certificate fingerprint. Реализованы операции `CATALOG_SEARCH`,
`GOODS_RESERVE`, `LOGISTICS_RESERVE`, `GOODS_COMMIT`, `LOGISTICS_COMMIT`,
`GOODS_RELEASE` и `LOGISTICS_RELEASE`.

Исходящий transport ограничивает connect timeout, размер ответа и fan-out.
Production-like environments отклоняют обычный HTTP; предусмотрены CA и
клиентские TLS certificate/key. Nginx ограничивает peer endpoint по IP,
burst и размеру тела. Replay одного входящего сообщения возвращает прежний
подписанный ответ, а изменённый payload с тем же id отклоняется.

`PeerProtocolExchange` хранит направления, документы, hashes, signatures,
fingerprints, результат и expiry. Успешные exchange evidence append-only;
runtime role не может удалить историю.

## Сага покупки

Покупатель создаёт `PurchaseIntent`, связанный с точными версиями offer и
quote и с canonical `summary_hash`. Затем:

1. home node товара блокирует ресурс, повторно проверяет доступность и создаёт
   signed goods receipt;
2. home node логистики независимо резервирует capacity и создаёт signed
   logistics receipt;
3. buyer node проверяет подпись, home node, intent, kind, resource, amount,
   unit, summary и expiry каждого receipt;
4. intent переходит в `PREPARED` только при наличии обоих удержаний;
5. commit сначала сохраняет подписанный запрос и состояние `COMMITTING`, затем
   собирает signed commit acknowledgements и завершает `COMMITTED`;
6. cancel сначала сохраняет причину и состояние `CANCELLING`, затем собирает
   signed release acknowledgements и завершает компенсацию.

Клиент браузера не может передать внешнюю подпись как доверенное доказательство:
buyer backend сам связывается с home node. При сетевом сбое устойчивые состояния
`COMMITTING` и `CANCELLING` остаются видимыми и повторяются с исходной
optimistic-lock версией. Commit/release на home node идемпотентны. Committed
удержание нельзя освободить командой release.

Worker автоматически переводит просроченные активные home-node holds и
незавершённые purchase intents в `EXPIRED`, освобождает reserved exposure и
добавляет signed expiration events. Истечение не зависит от открытого GUI.

## Лимиты и ответственность

Каждое внешнее удержание требует active bilateral limit для точного peer и
capability `CATALOG` или `LOGISTICS`. Под PostgreSQL lock проверяются
`max_package_value` и `max_unsettled_obligations`; `NodeExposure.reserved`
увеличивается на reserve, переносится в current на commit и уменьшается на
release/expiry.

Товар оценивается как количество, умноженное на unit price и обязательный сбор.
Логистика оценивается как сумма компонентов quote. Ответственность операции
атрибутируется именованному actor из исходного подписанного offer/quote event.
Паи обычного участника не списываются автоматически и не являются безусловным
залогом удалённого узла.

## Схема и журнал

Revision `0015_federated_discovery` создаёт offers, indexes, quotes, intents и
buyer-side receipts. Revision `0016_peer_protocol` добавляет authenticated
peer exchange evidence. Revision `0017_peer_reservations` добавляет home-node
resource holds, двухфазные signed evidence и recoverable состояния intent.
Текущий schema head: `0017_peer_reservations`.

В журнал входят публикация/отзыв offer, index, quote, создание intent,
goods/logistics receipt, запрос commit/cancel, итог commit/compensation, а также
home-node reserve/commit/release/expiry. Signed evidence защищены DB triggers;
populated downgrade блокируется.

## GUI и демоданные

Раздел `Поиск товаров и доставки` показывает источник, freshness, подпись,
количество, маршрут, полную стоимость и раскрываемую формулу. Из результата
создаётся intent; отдельная очередь показывает обе стадии резерва, recovery
состояния и receipts. Команды недоступны для stale, неподписанного или
неполного предложения.

Идемпотентный demo seed создаёт локальные и импортированные предложения
капусты, гвоздей и молока, локальные logistics quotes и подписанный peer index.
Внешние `.invalid` endpoints не вызываются seed-процессом; реальная
межузловая операция требует настроенного peer certificate, endpoint, contract
и bilateral limits.

## Проверки

- fresh migration `0001 -> 0017`: PASS;
- `alembic check`: изменений не требуется;
- Ruff и format check: PASS;
- strict mypy: 213 source files, PASS;
- backend: 143 tests, PASS;
- backend coverage: 76.08%, порог 75%;
- frontend: 45 test files / 108 tests, PASS;
- frontend coverage: 82.35% statements, 70.29% branches, 75.87% functions,
  88.59% lines;
- frontend typecheck и production PWA build: PASS;
- OpenAPI: 242 paths, из них 64 под `/api/v1/federation`;
- backend/frontend OpenAPI SHA-256:
  `A99952418FE1F4844C9A4C01CDE43A1083CDFF845661BEECC6ABEB162F1E25F4`.

Интеграционные тесты покрывают подписи и replay, fan-out, импорт результатов,
автоматический remote reserve/commit без клиентской подписи, oversell,
bilateral exposure, compensation, expiry worker и recovery версий GUI/API.

## Незакрытая граница

До завершения Slice 14 система не объявляет распределённую сделку погашенной
между узлами. Требуются три независимо развёрнутых узла, federated obligations,
signed snapshots, prepare receipts, approvals всех affected home nodes, commit
certificate, idempotent local apply, восстановление после потери coordinator и
полная reconciliation. Внешние production-критерии Slice 12 также остаются
обязательными.
