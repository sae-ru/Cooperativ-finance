# Клиринговый алгоритм

Статус: production-контракт Crisis MVP.

## Граница

MVP поддерживает двусторонний зачёт, простые циклы, частичный зачёт и
исключение спорных позиций. Алгоритм не создаёт новый долг и не превышает
исходное обязательство или кредитный лимит.

## Чистая функция

```text
ClearingResult calculate(ClearingInput input, AlgorithmVersion version)
```

Библиотека не читает БД, время, сеть или случайные значения. Все данные,
порядок и параметры входят в canonical input.

## Вход

- `cycle_id`;
- ordered obligations;
- debtor, creditor, subject/valuation unit;
- eligible remaining amount;
- liquidity class;
- dispute and freeze flags;
- credit and exposure limits;
- policy version;
- rounding and minimum residue rules;
- deterministic ordering key.

Перед расчётом API строит snapshot, блокирует включённые обязательства или
фиксирует их версии и вычисляет `input_hash`.

## Алгоритм MVP

1. Отфильтровать неподтверждённые, спорные, просроченно-заблокированные и
   несовместимые обязательства.
2. Сгруппировать совместимые единицы оценки и правила исполнения.
3. Выполнить двусторонний зачёт встречных обязательств.
4. Построить ориентированный граф оставшихся обязательств.
5. Обходить простые циклы в стабильном порядке участников и обязательств.
6. Для цикла взять минимум доступных остатков и лимитов риска.
7. Применить зачёт, повторять до отсутствия допустимого улучшения или достижения
   документированного лимита итераций.
8. Применить округление один раз на уровне entry по policy version.
9. Вернуть незакрытые остатки и причины исключения.

Оптимизация глобального максимума не является целью первого MVP. Приоритеты
ликвидности и справедливости должны быть явно версионированы.

## Доказательство

`ClearingProof` содержит algorithm id/version, input hash, parameters hash,
ordered entries before/after, excluded entries with reasons, totals,
rounding residues, result hash и подпись узла.

Повторный расчёт той же версией на том же canonical input обязан дать тот же
байтовый canonical result.

## Жизненный цикл

`DRAFT -> PREVIEWED -> DISPUTE_WINDOW -> FINALIZED`

Дополнительные: `CANCELLED`, `DISPUTED`, `SUPERSEDED`.

Preview не изменяет обязательства. Finalize в одной транзакции проверяет версии
входа, применяет entries, создаёт события по затронутым обязательствам и
финальное событие цикла.

## Инварианты

- `0 <= cleared <= remaining_before`;
- сумма уменьшений согласована по сторонам и единице;
- спорное обязательство имеет cleared = 0;
- входной объект не меняется во время finalize;
- один cycle не финализируется дважды;
- лимит не превышается ни в промежуточной позиции;
- результат не зависит от порядка строк БД;
- rounding residue объясним и не присваивается оператору.

## Тестирование

- golden vectors для каждой algorithm version;
- property tests сохранения сумм и границ;
- permutation tests независимости от входного порядка;
- повторный расчёт на Python и независимом verifier;
- concurrency test изменения обязательства между preview и finalize;
- adversarial graphs: self-loop, duplicate edge, zero, extreme decimal,
  disconnected components, dense cycles;
- миграционный тест проверки старого proof после обновления.
