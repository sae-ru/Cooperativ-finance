# ADR-0002: PostgreSQL как stateful core

Статус: Accepted.

## Context

Узел должен работать без cloud, broker и отдельного distributed database.

## Decision

PostgreSQL хранит operational state, signed journal, audit, outbox/inbox и read
models. Вложения хранятся файлово, но их metadata и hashes находятся в БД.

## Consequences

Redis/RabbitMQ/Elasticsearch не обязательны. Нужно тщательно управлять
индексами, partitioning и backup согласованностью blobs.

## Validation

Critical path и restore проходят при отсутствии всех необязательных сервисов.
