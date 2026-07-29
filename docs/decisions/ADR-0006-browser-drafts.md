# ADR-0006: Browser offline хранит только drafts

Статус: Accepted.

## Context

IndexedDB и service worker не обеспечивают серверные лимиты, роли, signatures
и согласованность хозяйственного реестра.

## Decision

При отсутствии локального API PWA сохраняет draft/pending request. Операция
возникает только после server validation, commit и выдачи `event_id`.

## Consequences

Нет полностью автономной экономической работы одного телефона. Нужен честный
offline UI и повторный review draft.

## Validation

E2E гарантирует, что draft не меняет balances и не выглядит accepted.

## Реализация Slice 42

Предложение товара или услуги хранится в IndexedDB как
`cooperative-browser-draft-v1`: `draft_id`, owner `user_id`, cooperative,
payload, optional Blob, timestamps, `authoritative=false` и
`review_required=true`. Retention — семь дней.

Рекурсивный validator запрещает server identity и повторно проверяет запись при
чтении. Reconnect не запускает replay: пользователь открывает draft для review
и отдельно публикует его. API/service worker остаются `NetworkOnly`.

Проверка: frontend `202` tests; живой save/reload/review сохранил PostgreSQL
signed event count `434 -> 434`.
