# ADR-0010: Cryptographic baseline

Статус: Proposed до OD-012/OD-013.

## Context

Нужны подписи, hash chain, password hashing и encrypted backups без собственной
криптографии и cloud KMS.

## Decision

Baseline: Ed25519, SHA-256, Argon2id, AES-256-GCM или ChaCha20-Poly1305,
versioned canonical JSON. Реализация только maintained libraries.

## Consequences

Юрисдикция может потребовать иной certified profile. Точная канонизация,
parameters и key containers должны быть утверждены до signed journal.

## Validation

Independent test vectors, negative signatures, rotation/revocation migration.
