# ADR-0003: Актуальное состояние и подписанный журнал

Статус: Accepted.

## Context

Полный event sourcing усложняет миграции и эксплуатацию, но системе нужна
неизменяемая доказательная история.

## Decision

Нормализованные таблицы содержат актуальное состояние. `signed_events` хранит
добавляемые факты с hash chain и signatures. Исправление создаёт compensation.

## Consequences

Нужно транзакционно сохранять оба контура и регулярно проверять согласованность.
Read models могут перестраиваться, но operational state не восстанавливается из
неполного набора событий без отдельной процедуры.

## Validation

State/event/audit/outbox commit атомарен; runtime не имеет UPDATE/DELETE журнала.

Начиная с revision `0038_atomic_event_outbox`, deferred constraint trigger на
`signed_events` не позволяет завершить commit без одной NODE signature и одной
canonical outbox row. Независимый verifier начинает со всех событий, поэтому
missing signature/outbox не скрывается join-ом.
