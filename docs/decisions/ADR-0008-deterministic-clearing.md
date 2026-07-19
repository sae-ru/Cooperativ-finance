# ADR-0008: Детерминированный clearing engine

Статус: Accepted.

## Context

Клиринг должен быть объяснимым, воспроизводимым и проверяемым старой версией.

## Decision

Clearing является чистой версионированной функцией canonical input. Нет DB,
network, clock или randomness внутри алгоритма. Результат имеет proof/hash.

## Consequences

Snapshot строится отдельно; каждое изменение rules создаёт algorithm version.

## Validation

Golden, property, permutation и independent verifier tests.
