# Операционный контур клиринга

Статус: обязательный production-контракт вокруг clearing engine.

## Назначение

`clearing_algorithm.md` определяет чистый расчёт. Этот документ определяет, как
люди и система формируют вход, проверяют позиции, открывают спор, финализируют
цикл и отвечают за ошибки.

Клиринг не является платёжной системой и не создаёт обязательство из воздуха.
Он уменьшает подтверждённые встречные обязательства в пределах их остатка,
совместимости, кредитного лимита и заранее принятой policy.

## Роли

| Роль | Полномочия | Не может |
|---|---|---|
| Участник | видеть свою позицию, подтвердить вход, спорить | менять чужие позиции |
| Клиринговый оператор | подготовить cycle и preview | единолично менять policy/input после подписей |
| Контролёр клиринга | проверить snapshot, exclusions, proof | быть автором спорного ручного исправления |
| Risk controller | подтвердить limits/exposure | менять обязательства |
| Финализатор | выполнить подписанный finalize | финализировать собственный unresolved conflict |
| Аудитор | воспроизвести proof и проверить журнал | изменять cycle |
| Администратор узла | обеспечить runtime/keys | редактировать хозяйственный результат |

Для малого цикла policy может объединить контролёра и финализатора, но не
оператора подготовки и единственного независимого проверяющего. Порог и состав
подписей версионируются.

## Объекты

- `ClearingMembership`: допуск участника и единицы оценки;
- `ClearingPosition`: входящие/исходящие eligible остатки и credit exposure;
- `ClearingPolicy`: algorithm, rounding, liquidity classes, limits, dispute SLA;
- `ClearingCycle`: период и state machine;
- `ClearingInputSnapshot`: неизменяемый ordered input;
- `ClearingPreview`: рассчитанный, но не применённый результат;
- `ClearingEntry`: изменение одного обязательства;
- `ClearingDispute`: спор по entry/input/eligibility;
- `ClearingApproval`: подпись summary hash;
- `ClearingProof`: воспроизводимое доказательство;
- `ClearingStatement`: понятная участнику выписка до/после.

## Жизненный цикл

```text
DRAFT
  -> COLLECTING
  -> INPUT_FROZEN
  -> PREVIEWED
  -> REVIEW
  -> DISPUTE_WINDOW
  -> READY_TO_FINALIZE
  -> FINALIZED
  -> RECONCILED
```

Дополнительные состояния: `CANCELLED`, `DISPUTED`, `FAILED_FINALIZATION`,
`SUPERSEDED`.

## Подготовка входа

1. Оператор выбирает period, policy и valuation versions.
2. Система собирает obligations с их versions и remaining quantity/value.
3. Исключаются неподтверждённые, frozen, disputed и несовместимые позиции.
4. Проверяются credit limits, guarantees и aggregate related-party exposure.
5. Для каждой позиции сохраняется inclusion/exclusion reason.
6. Ordered snapshot получает canonical hash.
7. После `INPUT_FROZEN` его нельзя редактировать; изменение создаёт новый
   snapshot/version и отменяет старые approvals.

## Preview

Чистый engine получает только snapshot и policy. Preview не изменяет balance,
obligation или share exposure. Он содержит:

- позиции до/после;
- cleared и remaining;
- исключения с причинами;
- liquidity/limit effects;
- rounding residues;
- algorithm/input/policy hashes;
- предупреждения о концентрации и малой ликвидности.

## Проверка и окно спора

Контролёр воспроизводит result hash и проверяет input completeness. Участник
получает только свои детали и разрешённые агрегаты других сторон. Спор может
касаться существования обязательства, остатка, качества исполнения, valuation,
eligibility, limit или ошибки алгоритма.

Открытие обоснованного спора исключает затронутую entry из finalize либо
возвращает весь cycle к новому snapshot по policy. Спор не редактирует preview.

## Finalize

Finalize выполняется одной PostgreSQL-транзакцией:

1. Проверить state, approvals, dispute window и signatures.
2. Повторно проверить versions всех obligations/limits.
3. Заблокировать объекты в стабильном порядке.
4. Убедиться, что result hash соответствует approved preview.
5. Уменьшить eligible obligation остатки.
6. Зафиксировать clearing entries и обновить positions.
7. Создать события обязательств и `clearing.cycle_finalized`.
8. Сохранить proof, audit и outbox.

При любой ошибке откатывается весь finalize. Частично финализированный цикл
запрещён.

## GUI клирингового оператора

Основные экраны:

- календарь циклов и status/period;
- очередь готовности входа;
- таблица позиций с inclusion reasons;
- preview до/после с фильтрами по участнику и liquidity class;
- warnings limits/exposure/concentration;
- approvals и независимость ролей;
- disputes workspace;
- finalize confirmation с hashes и totals;
- proof verifier и statements;
- reconciliation/report.

Участник видит отдельную выписку: что он должен/получает до и после, что
зачтено, что осталось, почему позиция исключена и до какого времени можно
оспорить.

## API

```text
POST /clearing/cycles
POST /clearing/cycles/{id}/collect
POST /clearing/cycles/{id}/freeze-input
GET  /clearing/cycles/{id}/input
POST /clearing/cycles/{id}/preview
GET  /clearing/cycles/{id}/positions
POST /clearing/cycles/{id}/approvals
POST /clearing/cycles/{id}/disputes
POST /clearing/cycles/{id}/ready
POST /clearing/cycles/{id}/finalize
GET  /clearing/cycles/{id}/proof
POST /clearing/proofs/verify
GET  /clearing/cycles/{id}/statements/{member_id}
POST /clearing/cycles/{id}/reconcile
```

Команды требуют idempotency и expected version. Proof verify не изменяет
состояние.

## Ответственность

- оператор отвечает за заявленную полноту подготовки и ручные exclusions;
- контролёр отвечает за проверку snapshot/proof в пределах своей роли;
- владелец исходного обязательства отвечает за корректность своих facts;
- администратор узла отвечает за сохранность ключа и доступность, но не за
  экономическую policy;
- разработчик/релизная группа отвечает за соответствие algorithm version и
  test vectors по отдельной процедуре;
- имущественное последствие возникает только после causal assessment, а не из
  факта технической ошибки автоматически.

Для критических ролей действуют role bonds и max exposure. Clearing proof
подписывается узлом и обязательными людьми.

## Reconciliation

После finalize сверяются totals, obligations, statements, accounting export,
events и outbox. Расхождение открывает incident/dispute и при необходимости
compensation cycle. SQL-исправление entries без события запрещено.

## Production acceptance

- один и тот же snapshot даёт один result hash;
- participant statement согласован с proof;
- disputed entry не финализируется;
- изменение obligation после preview блокирует finalize;
- duplicate finalize не создаёт второй эффект;
- оператор не меняет input после approval;
- independent verifier принимает текущие и старые supported proofs;
- restore сохраняет возможность проверить cycle.
