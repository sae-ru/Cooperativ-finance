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
