# ADR-0005: Синхронизация пакетами событий

Статус: Accepted.

## Context

Узлы могут быть разделены долго и обмениваться данными на носителе. Репликация
таблиц не выражает хозяйственные конфликты и доверие узлов.

## Decision

Обмен выполняется подписанными packages с manifest, ordered events,
certificates, revocations, blobs и proofs. Импорт проходит verify/simulate/apply.

## Consequences

Требуются protocol versions и conflict policies. Critical conflict не получает
last-write-wins.

## Validation

Golden packages, replay/tamper tests и field drill двух разделённых узлов.
