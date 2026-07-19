# ADR-0004: Transactional outbox

Статус: Accepted.

## Context

Сбой между commit хозяйственной операции и публикацией события создаёт потерю
уведомления или пакета. Обязательный broker ухудшает автономность.

## Decision

Outbox записывается в той же PostgreSQL-транзакции. Workers выбирают rows с
lease/`SKIP LOCKED`, retry, idempotency и quarantine.

## Consequences

Доставка at-least-once; каждый consumer обязан быть идемпотентным. Lag
наблюдается локальными метриками.

## Validation

Crash tests до/после commit и конкурентные workers без duplicate effect.
