# Кризисный протокол

Статус: механизм Slice 10 реализован; конкретные пороги и исключения утверждает пилот.

## Назначение

Кризисный режим временно меняет разрешённые операции, лимиты и приоритеты по
заранее принятой policy. Он не даёт администратору неограниченную власть и не
переписывает договоры задним числом.

## Основания

- банковский/платёжный сбой;
- потеря внешней связи;
- дефицит критического ресурса;
- энергетический или логистический сбой;
- компрометация узла/ключей;
- массовое неисполнение;
- физический инцидент склада;
- несовместимость протокола узлов.

## Activation

Содержит crisis type, territory/scope, evidence, policy version, activating
mandate, independent approvers, start, mandatory review time, maximum duration
и exit criteria.

Один человек не активирует режим и не меняет rationing rule единолично.

## Возможности режима

- ограничить новые права, гарантии и кредит;
- увеличить требования к подтверждению;
- открыть offline epoch;
- приоритизировать критические ресурсы;
- включить rationing;
- заморозить спорные/скомпрометированные объекты;
- перейти на paper forms;
- ограничить federation import/export;
- включить усиленный audit и частые reserve snapshots.

Режим не может списать паи или отменить appeal без отдельного основания.

## Нормирование

Policy фиксирует ресурс, eligible population, protected minimum, household
aggregation, приоритеты, период, allocation formula, exceptions, appeal и
privacy. До confirm показываются rule version и aggregate consequences.

Allocation вычисляется детерминированно. Ручное исключение требует причины,
двух подписей и audit. Получение базового ration не создаёт долг.

## Резервы

Для критического ресурса хранятся target, physical verified amount, committed,
available, consumption rate, coverage days, quality/expiry и confidence.
Неverified stock не повышает reserve status.

## Review и продление

Автоматически напоминается о review, но режим не продлевается скрыто. Продление
является новым подписанным решением с обновлёнными facts. Истёкший mandate
переводит policy в безопасное заранее определённое состояние.

## Завершение

Перед закрытием выполняются inventory reconciliation, review ограничений,
ключей, конфликтов, компенсаций и outstanding paper forms. Итоговый отчёт
содержит события, решения, ущерб, помощь, исключения и corrective actions.

## Запрещено

- бессрочный кризис без review;
- скрытая смена приоритетов;
- дискриминационное правило без законного основания;
- единоличная выдача самому approving operator;
- удаление истории режима;
- использование crisis mode для обхода паевой экспозиции и апелляции.
## Реализованный контракт Slice 10

Реализация использует `ReserveTarget`, append-only `ReserveSnapshot`,
`CrisisMandate`, `CrisisReview`, versioned `RationingRule`, frozen
`RationingPlan`, `RationingAllocation`, evidence-backed `RationIssuance`,
нумерованный `CrisisPaperForm` и immutable `CrisisReport`.

Protected minimum сначала выделяется каждому eligible участнику. При нехватке
на сумму минимумов доступный остаток делится поровну; только последующий остаток
распределяется equal/weighted формулой до maximum per member. Confirm повторно
проверяет snapshot и совокупный резерв под cooperative lock.

Новая policy version атомарно переводит прежнюю в `RETIRED`. Rule нельзя
заменить с открытыми allocations, reserve target нельзя заменить во время его
использования активным мандатом. Истёкший mandate не даёт полномочий независимо
от задержки worker. Полный состав и доказательства: [implemented_slice_10.md](implemented_slice_10.md).
