# ADR-0007: Content-addressed evidence storage

Статус: Accepted.

## Context

Фото и документы раздувают БД, но должны восстанавливаться и проверяться вместе
с событиями.

## Decision

Encrypted blobs хранятся вне основных таблиц по SHA-256. PostgreSQL владеет
metadata, rights и references. S3 adapter опционален позднее.

## Consequences

Backup обязан согласовывать БД и blobs. Изменение файла создаёт новый blob.

## Validation

Restore проверяет каждый referenced hash и обнаруживает отсутствующий blob.
