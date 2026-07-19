# API

Статус: обязательные conventions для OpenAPI `/api/v1`.

## Общие правила

- JSON UTF-8; binary upload выполняется отдельным endpoint;
- snake_case в JSON;
- UUID передаётся строкой;
- UTC timestamp в RFC3339;
- Decimal передаётся строкой, не JSON number;
- количество всегда содержит `value`, `unit_code`, `scale`;
- create/transition endpoints требуют `Idempotency-Key`;
- критические команды возвращают `event_id`;
- клиент не передаёт вычисляемые роли, лимиты или итоговую экспозицию как
  доверенные значения;
- OpenAPI генерируется и проверяется на breaking changes.

## Аутентификация

- короткоживущий access token;
- server-side revocable refresh session;
- CSRF-защита при cookie transport;
- TOTP/WebAuthn step-up для критических ролей;
- service client использует отдельные credentials, scopes и network policy;
- внешний OIDC не является обязательным.

## Формат успешной команды

```json
{
  "data": {
    "id": "uuid",
    "status": "VERIFIED",
    "version": 3
  },
  "event_id": "uuid",
  "request_id": "uuid"
}
```

## Формат ошибки

```json
{
  "error": {
    "code": "INVENTORY_INSUFFICIENT_AVAILABLE_QUANTITY",
    "message_key": "errors.inventory.insufficient_available_quantity",
    "parameters": {
      "available": "12.000",
      "requested": "15.000",
      "unit_code": "kg"
    },
    "field_errors": [],
    "retryable": false
  },
  "request_id": "uuid"
}
```

Сообщение не раскрывает ключи, SQL, stack trace, персональные данные или
внутреннюю топологию.

## HTTP semantics

| Ситуация | Код |
|---|---|
| успешное чтение/изменение | 200 |
| создание | 201 |
| асинхронная некритичная задача | 202 |
| неверный ввод | 400/422 |
| нет сессии | 401 |
| недостаточно полномочий | 403 |
| объект не найден или скрыт политикой | 404 |
| конфликт состояния/version | 409 |
| нарушен лимит | 422 с domain code |
| rate limit | 429 |
| временная локальная деградация | 503 |

## Конкурентные изменения

Изменяемый ресурс возвращает `version` и ETag. Команда перехода передаёт
`expected_version`. Несовпадение возвращает `AGGREGATE_VERSION_CONFLICT` и
актуальную безопасную сводку.

## Списки и фильтры

- cursor pagination для событий и больших журналов;
- bounded `limit`, по умолчанию 50;
- stable sort с UUID tie-breaker;
- allowlist фильтров и сортировок;
- export является отдельной аудируемой операцией.

## Двухэтапное подтверждение

Критическая операция имеет ресурс approval:

```text
POST /operations/{type}/prepare
POST /approvals/{id}/attest
POST /approvals/{id}/finalize
POST /approvals/{id}/cancel
```

Prepare возвращает неизменяемый summary hash, экспозицию, требуемые роли,
истечение и human-readable preview. Attester не может быть тем же человеком,
если политика требует независимость.

## Вложения

1. Создать upload intent с размером, типом и ожидаемым SHA-256.
2. Передать stream с лимитом размера.
3. Сервер проверяет hash и сохраняет encrypted blob.
4. Команда ссылается на immutable evidence id.

Изменение файла создаёт новый blob id. Download проверяет права и журналируется.

## API modules

Полный перечень endpoints задан разделом 14 ТЗ. Реализация выполняется
вертикальными slices; endpoint не публикуется до domain, authorization,
idempotency, audit, OpenAPI и integration tests.

## Профильные API-контракты

- административный lifecycle: [admin_console.md](admin_console.md);
- операционный клиринг: [clearing_operations.md](clearing_operations.md);
- onboarding и lifecycle узла: [node_onboarding.md](node_onboarding.md);
- ответственность и exposure узла: [node_liability_policy.md](node_liability_policy.md);
- федеративный поиск и логистика: [federated_catalog_search.md](federated_catalog_search.md);
- межузловой клиринг: [inter_node_clearing.md](inter_node_clearing.md).

OpenAPI обязан реализовывать перечисленные там команды и scopes; общие CRUD
endpoints не заменяют state transitions, approvals и audit.
## Версионирование

- additive compatible change остаётся в `/api/v1`;
- удаление, смена смысла или типа требует `/api/v2` либо migration window;
- event schema и HTTP API версионируются независимо;
- deprecation содержит дату, замену и offline impact;
- скрытое изменение экономического правила под прежней версией запрещено.
