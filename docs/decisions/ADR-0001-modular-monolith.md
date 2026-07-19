# ADR-0001: Модульный монолит

Статус: Accepted.

## Context

Критические операции требуют атомарных изменений остатков, прав, паёв, событий
и аудита. Малой команде нужен offline deploy и простое восстановление.

## Decision

Один backend deployable с bounded modules и одной PostgreSQL. Модульные границы
проверяются импортами, владением таблиц и application contracts.

## Consequences

Плюсы: локальные транзакции, простой Compose, backup и debugging. Минусы:
нужна дисциплина границ; независимое масштабирование отложено.

## Validation

Architecture tests и отсутствие cross-module writes.
