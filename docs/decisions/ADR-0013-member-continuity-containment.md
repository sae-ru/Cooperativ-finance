# ADR-0013: Member continuity containment before economic succession

Статус: Accepted для code-level containment; economic succession остаётся Proposed до OD-019, OD-033 и OD-038.

## Context

На `identity.members` ссылаются identity, assets, exchange, federation, risk, solidarity, trust и signed journal. Одинаковый `member_id` означает разные вещи: текущего владельца, сторону незакрытого обязательства, получателя помощи или исторического автора решения. Массовая замена ссылок уничтожила бы происхождение фактов и могла бы передать ответственность без законного основания.

При добровольном выходе, смерти или недееспособности новые действия должны остановиться немедленно, но подтверждение обстоятельства и передача прав требуют разных ответственных людей.

## Decision

1. Создаётся versioned continuity case типа `VOLUNTARY_EXIT` или `DEATH_OR_INCAPACITY`.
2. Request атомарно переводит Member в `EXIT_PENDING` или `DECEASED_OR_INCAPACITATED`, отключает связанные Users, отзывает активные Sessions и приостанавливает активные Memberships.
3. Сервис сохраняет минимальный versioned access snapshot. Он нужен только для безопасного отклонения ошибочной заявки и не содержит паролей, токенов, identifiers или открытых персональных данных.
4. Решение принимает другой персональный permanent `SECURITY_ADMIN` с TOTP step-up. Подтверждённая смерть/недееспособность переводит Member в `SUCCESSION_REVIEW`; добровольный выход остаётся `EXIT_PENDING`.
5. Rejection восстанавливает только записи, чьи версии и contained-state не менялись после request. Сессии не восстанавливаются.
6. Все внешние ссылки группируются в безопасную сводку. Ни одна экономическая или историческая ссылка автоматически не переносится.
7. Закрытие участника, передача паёв, прав, долгов, поручительств, репутации, custody и наследства выполняются будущими доменными workflows по утверждённой policy, а не общей continuity-командой.

## Consequences

- Ошибочная общая форма не может переписать signed history.
- Потеря доступа происходит сразу, а независимое решение остаётся обязательным.
- Rejection не оживляет украденный или уже выданный bearer token.
- Карточка в `SUCCESSION_REVIEW` или `EXIT_PENDING` остаётся видимой и несёт все прежние обязательства до доменного settlement.
- Cross-cooperative succession и наследование не объявляются закрытыми.

## Validation

- PostgreSQL migration с partial unique pending-case index и fail-closed downgrade после continuity history.
- Integration tests на containment, token rejection, independent TOTP decision, version-safe rollback и сохранение economic/history references.
- RU/EN operator GUI показывает причины, последствия и сгруппированные ссылки без SQL names и PII.
- Signed journal, audit и idempotency обязательны для request и decision.