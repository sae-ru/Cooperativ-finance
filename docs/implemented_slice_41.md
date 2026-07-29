# Реализованный Slice 41: атомарное событие и outbox

Статус: критерий приемки 127 закрыт исполняемым PostgreSQL-инвариантом,
аварийными integration tests и проверкой работающего Docker-узла.

## Гарантия commit

`SignedJournalService.append` в одной `AsyncSession` сохраняет signed event,
node signature, outbox message и новый hash-chain head. Миграция
`0038_atomic_event_outbox` добавляет последнюю независимую линию защиты:

- до установки проверяет всю существующую историю;
- разрешает ровно одну node-подпись и одну outbox-запись на событие;
- отложенный constraint trigger выполняется в момент `COMMIT`;
- trigger сверяет node/key purpose и все canonical поля outbox payload;
- неполная тройка event/signature/outbox получает SQLSTATE `23514`, а вся
  транзакция, включая доменное состояние и chain head, откатывается.

Trigger не требует раннего порядка flush: parent event может быть записан до
signature/outbox внутри транзакции, но не может существовать без них после
успешного commit.

## Независимость от worker

Worker блокирует только `outbox_messages` через
`FOR UPDATE OF outbox_messages SKIP LOCKED`; immutable `signed_events`
используется read-only. Перед квитанцией сверяются topic, event id, event type,
event hash, node id и local sequence. `ConsumerReceipt` и состояние
`PUBLISHED` фиксируются одной worker-транзакцией.

Остановка worker после хозяйственного commit оставляет сообщение `PENDING` и
не отменяет операцию. Остановка после построения квитанции, но до worker commit,
откатывает и квитанцию, и lease/status. После запуска несколько workers
конкурируют безопасно, а unique receipt не допускает повторного эффекта.

## Независимая проверка

`verify_journal` больше не скрывает событие через inner join. Он начинает со
всех событий узла и отдельно требует:

- ровно одну NODE signature;
- ровно одну outbox row;
- canonical topic и payload;
- прежние hash-chain, payload hash и Ed25519 checks.

Поврежденный payload получает `OUTBOX_PAYLOAD_INVALID`, worker переводит его в
`QUARANTINED` без consumer receipt.

## Проверка

- три fault tests: incomplete commit, worker rollback/restart/concurrency,
  outbox tamper/quarantine/recovery;
- весь journal/observability модуль: `10 passed`;
- migration `0038 -> 0037 -> 0038` на заполненной БД: `2921` events,
  `0` incomplete, trigger enabled;
- `alembic check`: no new upgrade operations;
- рабочий Docker-узел: revision `0038_atomic_event_outbox`, `434` events,
  `434` NODE signatures, `434` outbox rows, `434` receipts, `0` pending,
  `0` quarantined;
- `coopctl verify-journal`: `ok=true`, sequence `434`, failures `[]`;
- `/health/ready`: `READY`, database/blob/key `UP`;
- при остановленном live worker readiness остался `READY`, operational status
  честно стал `DEGRADED/STALE`; после recreate worker вернулся в
  `OPERATIONAL/RUNNING`, outbox остался `434/434` без pending/quarantine.

Внешний брокер в локальный critical path не добавлен. Если transport adapter
будет введен после нагрузочных испытаний, он обязан читать ту же durable outbox
и сохранять отдельную идемпотентную delivery receipt. Target-host power-loss и
remote CI на конкретном release остаются отдельными production evidence.