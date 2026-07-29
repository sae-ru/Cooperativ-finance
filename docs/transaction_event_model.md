# Транзакции и события

Статус: обязательный контракт записи.

## Граница транзакции

Одна критическая команда выполняет в одной PostgreSQL-транзакции:

1. проверку idempotency;
2. чтение и блокировку агрегатов;
3. проверку ролей, лимитов, состояния и экспозиции;
4. изменение операционных таблиц;
5. добавление signed event;
6. добавление audit record;
7. добавление outbox records;
8. фиксацию idempotent response.

Частичный commit запрещён.

Migration `0038_atomic_event_outbox` проверяет это на границе БД. Deferred
constraint trigger разрешает промежуточный flush события, но в момент commit
требует ровно одну NODE signature и ровно одну canonical outbox row. Поэтому
ошибка приложения, прямой SQL или injected failure не могут оставить
зафиксированное событие без доставки; нарушение откатывает и доменные строки.

## Уровень изоляции и блокировки

По умолчанию используется `READ COMMITTED` с явными row locks и условными
UPDATE. Для расчётов, затрагивающих несколько конкурирующих остатков или паёв,
допускается `SERIALIZABLE` с ограниченными повторами всей команды.

Порядок блокировок стабилен:

1. cooperative/policy version;
2. share and credit accounts по UUID;
3. lots/reservations по UUID;
4. rights/obligations по UUID;
5. aggregate root.

Это уменьшает deadlocks. Повтор команды выполняется только по известному
retryable SQLSTATE и сохраняет тот же idempotency key.

## Idempotency

Ключ имеет scope `(actor_id, endpoint, idempotency_key)` и hash канонического
request body. Повтор с тем же hash возвращает сохранённый статус и body. Повтор
с другим hash возвращает `IDEMPOTENCY_KEY_REUSED`.

Идемпотентность не заменяет уникальные ограничения домена.

## Envelope события

```json
{
  "event_id": "uuid",
  "event_type": "inventory.lot_attested",
  "schema_version": 1,
  "protocol_version": "1.0",
  "node_id": "uuid",
  "local_sequence": 42,
  "aggregate": {"type": "inventory_lot", "id": "uuid", "version": 3},
  "actor": {
    "person_id": "uuid",
    "organization_id": "uuid",
    "role_assignment_id": "uuid"
  },
  "occurred_at": "RFC3339 UTC",
  "payload": {},
  "evidence": [],
  "previous_event_hash": "sha256:hex",
  "payload_hash": "sha256:hex",
  "signatures": []
}
```

`recorded_at` является локальным полем хранения и не входит в утверждение
исполнителя. Исправление часов фиксируется отдельным событием.

Для event type из канонического critical registry payload обязательно содержит
`_command_assurance.format = critical-command-assurance-v2`. Снимок включает
проверенные `performed_by`, `on_behalf_of`, role/scope, evidence digest, точную
exposure, attesters, approvers и `next_responsible`. И payload, и полный список
evidence входят в canonical envelope, поэтому любое изменение обнаруживается
проверкой hash/signature.

Формат v1 и события до введения реестра остаются неизменной историей. Они не
backfill-ятся через UPDATE и рассматриваются как legacy findings при
операторском review.

Для lifecycle роли request передаёт следующий шаг cooperative/node scope,
activation и approval — целевому member, rejection и revoke — обратно владельцу
scope. Requester и независимый decider сохраняются как attester/approver, а
изменение `RoleAssignment` и signed event фиксируются одной транзакцией.

Для trust/crisis команд exposure различает `GOVERNANCE`, `SANCTION`,
`REPUTATION` и `CRISIS`. Решения сохраняют независимого actor как approver,
предыдущих участников цепочки как attesters и затронутого member/cooperative
как `next_responsible`. Положительные количества резервов и выдачи содержат
exact Decimal и unit; нулевой физический остаток остаётся доказанным payload
без ложной положительной exposure.

Для node authority представляемой стороной является локальный `NODE`, а
внешний узел и все его действующие именованные ответственные входят в
`next_responsible`. Onboarding сохраняет принятие ролей и challenge history;
trust/limit/bond — terms/evidence и точный maximum loss; incident/key lifecycle
— fingerprints, continuity и независимое решение; offline/exposure — внешний
node, лимиты, reconciliation, Decimal amount и unit.

## Канонизация и подпись

- подписывается каноническое представление envelope без `signatures`;
- профиль канонизации версионируется;
- неизвестное обязательное поле приводит к отказу импорта;
- хеш вложения подписывается как часть evidence ref;
- подпись проверяется с учётом действия ключа на `occurred_at` и статуса отзыва;
- отзыв не делает историческую подпись недействительной до момента
  компрометации, установленного инцидентом.

## Последовательность узла

`local_sequence` выдаётся транзакционно и монотонно для узла. Пропуск допустим
только с подписанным служебным объяснением. Цепочка `previous_event_hash`
позволяет обнаружить удаление, вставку и перестановку.

## Outbox worker

Worker выбирает готовые строки небольшими пакетами с `FOR UPDATE SKIP LOCKED`,
фиксирует lease, обрабатывает идемпотентно и записывает результат. После
ограниченного числа попыток запись получает `QUARANTINED`; хозяйственная
транзакция остаётся действительной, а оператор получает alert.

Блокировка применяется только к `outbox_messages`. Связанное immutable событие
читается для сверки `event_id`, `event_type`, `event_hash`, `node_id` и
`local_sequence`. Consumer receipt и `PUBLISHED` находятся в одной worker
transaction: crash до commit откатывает оба, а повторный worker безопасно
забирает `PENDING` или строку с истекшим lease.

## Inbox и импорт

Импорт никогда не вызывает внешние side effects до завершения проверки.

```text
RECEIVED -> VERIFIED -> SIMULATED -> APPLIED
                     -> CONFLICT
         -> REJECTED
```

Проверяются подпись package, сертификаты, protocol/schema versions, replay,
последовательность, payload hashes, зависимости, лимиты offline epoch и
доменные инварианты.

## Компенсации

Компенсирующее событие содержит ссылку на исправляемое событие, основание,
решение, величину исправления и новых ответственных. Оно не изменяет payload
исходного события и не маскируется обычным UPDATE.

## Read models

Read model можно удалить и перестроить из операционных таблиц и журнала. У него
есть `projection_version` и cursor последнего события. Отчёт показывает время
актуальности. Read model не подписывает хозяйственное утверждение.

## Заранее выделенный event UUID

Команда, которая должна записать event FK во все изменяемые строки, может заранее
выделить UUID и передать его `SignedJournalService.append(event_id=...)`. До
вызова append этот UUID ставится в state rows; journal validation выполняется
внутри `session.no_autoflush`, затем explicit flush видит и event, и state.

FK должен быть `DEFERRABLE INITIALLY DEFERRED`, а commit-level journal trigger
по-прежнему требует NODE signature и outbox. Эта схема используется адресной
книгой, где одна команда может одновременно снять прежний default и создать
новый. UUID не считается событием до успешного commit; любой отказ откатывает
весь набор строк.
