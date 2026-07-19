# Федеративный поиск товаров и логистики

Статус: production-архитектура post-pilot federation contour.

## Назначение

Пользователь локального узла должен находить предложения других доверенных
узлов, сравнивать товар вместе с доставкой и создавать проверяемое намерение
сделки. Поиск не является общей центральной базой остатков и не создаёт
резервирование сам по себе.

## Топология поиска

Поддерживаются одновременно три режима:

1. `DIRECT`: локальный узел запрашивает разрешённых peers.
2. `INDEXED`: региональный directory кэширует подписанные offer indexes узлов.
3. `CACHED_OFFLINE`: используется последний проверенный snapshot с явной
   давностью и без обещания текущей доступности.

Региональный индекс является ускорителем, а не источником истины. Каждая запись
проверяется подписью публикующего узла и может быть перепроверена у home node.

## Публикуемое предложение

`FederatedOffer` содержит:

- `offer_id`, version и home node;
- owner/seller organization;
- standard product/service code и локальное описание;
- quantity или допустимый publish band;
- unit, scale, minimum batch и divisible flag;
- quality grade, certificates и evidence hashes;
- origin warehouse/region с privacy precision;
- availability window и fulfillment deadline;
- base price/valuation, unit и price policy version;
- налоги/обязательные сборы, если известны;
- условия выдачи, упаковки, температуры и handling;
- допустимые counterparties и geography;
- требуемые guarantee/credit условия;
- `valid_until`, node sequence и signed timestamp;
- signature узла и ответственного business publisher.

Публикация не раскрывает точный критический резерв, если policy разрешает
только диапазон. Остаток локальной партии остаётся на home node.

## Свежесть и состояние

Поисковый результат имеет один из статусов:

- `LIVE_VERIFIED`: подтверждён home node в текущем запросе;
- `SIGNED_CACHED`: подпись валидна, но используется опубликованный snapshot;
- `STALE`: срок свежести превышен, доступен только как справка;
- `REVOKED_OR_UNTRUSTED`: не показывается как доступное предложение.

Время обновления, срок действия и источник видны пользователю. Stale offer не
может перейти к reservation без повторной проверки home node.

## Запрос поиска

Минимальные фильтры:

- product/service code и допустимые substitutes;
- quantity/unit и quality minimum;
- destination и latest delivery;
- maximum goods/landed cost;
- допустимая давность данных;
- trusted nodes/territory;
- required certificates;
- partial fulfillment;
- guarantee/credit compatibility;
- crisis restrictions.

Локальный узел нормализует единицы только по версионированному справочнику.
Несопоставимые valuation units не сортируются как одна цена без явной conversion
policy.

## Логистическая котировка

Для выбранных top-K товарных предложений узел запрашивает `LogisticsQuote` у
локальных или внешних логистических узлов. Запрос содержит origin, destination,
quantity, weight/volume, packaging, temperature, pickup/delivery windows,
custody requirements и declared value/risk class.

Quote содержит:

- carrier/logistics node и ответственных;
- route legs и transfer points;
- доступную capacity и reservation terms;
- pickup/delivery estimate;
- base, distance, handling, storage и mandatory fee components;
- insurance/guarantee component, если он реально оплачивается;
- currency/valuation и rounding policy;
- maximum liability и logistics bond;
- `valid_until`, assumptions и signature.

Неизвестная стоимость обозначается `ESTIMATED`, а не нулём.

## Полная стоимость до места назначения

```text
goods_cost = unit_price * requested_quantity
logistics_cost = transport + handling + transfer + storage
mandatory_cost = taxes_and_required_fees + paid_insurance
landed_cost = goods_cost + logistics_cost + mandatory_cost
```

Expected loss, reliability и confidence не добавляются скрыто в `landed_cost`.
Они показываются отдельными колонками/фильтрами. Если policy вводит платный risk
reserve, он отображается отдельной строкой и входит в mandatory cost.

## Сортировка

Пользователь выбирает режим:

- минимальная подтверждённая landed cost;
- минимальный срок доставки;
- наивысшее качество;
- минимальное число custody transfers;
- максимальная свежесть/уверенность;
- локальный/региональный приоритет по опубликованной policy;
- multi-criteria с видимыми весами.

По умолчанию используется `landed_cost`, затем delivery time, freshness и
stable offer id. Результат с estimated logistics не смешивается без отметки с
полностью подтверждённой ценой.

## Производительность запроса

Сначала фильтруются предложения товара, затем логистические quotes запрашиваются
только для top-K кандидатов и нескольких допустимых маршрутов. Это исключает
полный декартов запрос offer x carrier. Кэш quote ограничен его `valid_until` и
точным hash параметров маршрута.

## Подготовка сделки и reservation saga

Поиск не блокирует товар и транспорт. После выбора запускается saga:

1. Buyer node создаёт `PurchaseIntent` с offer/quote versions.
2. Seller home node повторно проверяет trust/limit и резервирует товар до expiry.
3. Logistics node резервирует capacity и возвращает signed reservation.
4. Buyer node проверяет landed cost, credit/guarantee и показывает final preview.
5. Buyer подтверждает canonical summary.
6. Seller и logistics nodes получают commit intent и создают локальные events.
7. Buyer получает signed receipts и создаёт Deal/Obligations.
8. Timeout/отказ запускает compensating releases.

Распределённая SQL-транзакция и блокировка на часы не применяются. До получения
всех required receipts сделка имеет `PREPARED`, но не `ACTIVE`.

## GUI

Результат показывает:

- товар и качество;
- seller/home node и trust status;
- доступное/минимальное количество;
- цену товара;
- логистику с разбивкой;
- полную landed cost;
- срок и route summary;
- freshness и confirmed/estimated status;
- guarantee/credit требования;
- действия «Проверить доступность» и «Подготовить сделку».

Фильтры и сортировка не скрывают компоненты цены. Пользователь может раскрыть
формулу, версии offer/quote и ответственных.

## API

```text
POST /federation/offers/publish
POST /federation/offers/revoke
POST /federation/catalog/search
POST /federation/catalog/offers/{id}/verify
POST /federation/logistics/quotes
POST /federation/logistics/quotes/{id}/verify
POST /federation/purchase-intents
POST /federation/purchase-intents/{id}/reserve-goods
POST /federation/purchase-intents/{id}/reserve-logistics
POST /federation/purchase-intents/{id}/commit
POST /federation/purchase-intents/{id}/cancel
GET  /federation/purchase-intents/{id}/receipts
```

## Безопасность и антифрод

- offer/quote подписываются и имеют expiry;
- publisher capability и node trust проверяются;
- массовая ложная публикация, bait pricing и отмены создают antifraud signals;
- directory не может изменить offer без разрушения подписи;
- query privacy ограничивает раскрытие полного спроса внешним узлам;
- критические резервы не публикуются сверх policy;
- ranking version, weights и sponsored status прозрачны;
- платное продвижение запрещено в crisis search либо явно отделено и не влияет
  на emergency priority.

## Acceptance

- один запрос объединяет подписанные offers нескольких узлов;
- результат показывает home node, freshness и signature status;
- landed cost воспроизводима из компонентов;
- estimated logistics не выдаётся за confirmed;
- stale offer не резервируется без home verification;
- сортировка стабильна для одинаковых данных и версии;
- поиск не изменяет остатки;
- товар и logistics capacity резервируются отдельно с expiry;
- failed saga освобождает reservations компенсирующими events;
- directory outage не блокирует direct/cached local operation.
