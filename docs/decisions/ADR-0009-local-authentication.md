# ADR-0009: Локальная идентификация обязательна

Статус: Accepted.

## Context

Внешний OIDC, email и SMS могут исчезнуть одновременно с финансовой/сетевой
инфраструктурой.

## Decision

Каждый node поддерживает local accounts, revocable server sessions, step-up и
dual-control recovery. OIDC является дополнительным adapter.

## Consequences

Узел отвечает за password security, account recovery и revocation. Identity
continuity между узлами требует отдельного protocol.

## Validation

Полный вход/recovery/key revoke drill без Интернета.
