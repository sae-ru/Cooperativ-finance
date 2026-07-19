# Границы модулей

Статус: обязательный контракт структуры backend.

## Группировка

Физически код хранится в одном backend, но модули сгруппированы в bounded
contexts. Группа не даёт права обходить application API соседнего модуля.

| Контекст | Модули | Владеет данными |
|---|---|---|
| Identity | `auth`, `members`, `cooperatives` | пользователи, членство, роли, сессии |
| Assets | `catalog`, `inventory`, `quality`, `rights` | товары, партии, качество, права |
| Exchange | `deals`, `obligations`, `logistics`, `clearing` | сделки, исполнения, доставка, циклы |
| Risk | `credit_limits`, `shares`, `personal_accountability`, `risk_antifraud` | лимиты, паи, экспозиции, ответственность |
| Trust | `reputation_risk`, `sanctions_appeals`, `audit`, `arbitration` | сигналы, решения, споры, аудит |
| Solidarity | `solidarity`, `reserves`, `crisis_protocol` | фонды, помощь, резервы, режимы |
| Node | `offline_operations`, `node_trust` | узлы, ключи, epochs, sync inbox/outbox |
| Federation | `federated_catalog`, `federated_logistics`, `federation_directory`, `inter_node_clearing` | published offers, quotes, trust directory, federated cycles |
| Reporting | `reports` | перестраиваемые read models и экспорты |

## Допустимые зависимости

```text
Reporting -> все публичные read contracts
Trust -> Identity, Exchange, Risk
Solidarity -> Identity, Assets, Risk, Trust
Exchange -> Identity, Assets, Risk
Assets -> Identity
Risk -> Identity
Federation -> Identity, Assets, Exchange, Risk, Node
Node -> event contracts всех модулей
```

Обратная зависимость запрещена. Для реакции нижнего контекста на верхний
используется событие или application-level orchestration.

## Структура модуля

```text
module/
  api.py
  commands.py
  queries.py
  application.py
  domain/
    entities.py
    value_objects.py
    policies.py
    events.py
    errors.py
  ports.py
  infrastructure/
    models.py
    repositories.py
  tests/
```

Маленькие модули могут объединять файлы, но направление зависимостей остаётся.

## Публичный контракт модуля

Модуль публикует только command/query DTO, application service interface,
event schemas, стабильные identifiers и документированные domain errors.
SQLAlchemy models и repository implementations соседям не экспортируются.

## Владение изменениями

| Действие | Единственный владелец |
|---|---|
| подтвердить партию | Assets/quality |
| изменить доступный остаток | Assets/inventory |
| выпустить или погасить право | Assets/rights |
| создать обязательство | Exchange/deals |
| подтвердить исполнение | Exchange/obligations |
| заблокировать пай | Risk/shares |
| определить экспозицию | Risk/personal_accountability |
| финализировать клиринг | Exchange/clearing |
| назначить санкцию | Trust/sanctions_appeals |
| распределить помощь | Solidarity/solidarity |
| активировать кризис | Solidarity/crisis_protocol |
| принять sync package | Node/offline_operations |

## Межмодульная согласованность

Критические изменения выполняются одной PostgreSQL-транзакцией через
orchestrating application service. Доменное событие не имитирует eventual
consistency там, где требуется атомарная блокировка остатка, пая или лимита.

После commit outbox используется для уведомлений, read models, отчётов,
подготовки sync packages и некритичных антифрод-сигналов.

## Проверка границ

- dependency tests запрещают импорты infrastructure соседей;
- архитектурный тест проверяет отсутствие cross-module writes;
- миграция назначается одному модулю-владельцу;
- публичное событие имеет JSON Schema и версию;
- изменение поля контракта проходит ADR.
