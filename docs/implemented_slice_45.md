# Реализованный Slice 45: сквозная ответственность адресной книги

Дата проверки: 2026-07-29.

Статус: исправлен обнаруженный разрыв между пользовательской командой адресной
книги и подписанным журналом. Ранее API возвращал audit UUID в поле `event_id`;
теперь возвращается идентификатор реального signed event.

## Транзакционный контракт

Создание, изменение и архивирование адреса одной транзакцией фиксируют:

1. idempotency record;
2. состояние `identity.participant_addresses`;
3. signed event, NODE-подпись и canonical outbox;
4. audit со ссылкой `signed_event_id`;
5. idempotent response с тем же event UUID.

Команда заранее резервирует UUID события. Все изменяемые адресные строки получают
его до flush, а журнал принимает этот UUID. Deferred FK проверяется на commit.
Это устраняет окно, в котором ORM мог записать смену адреса по умолчанию раньше
события.

Revision `0039_participant_address_events` добавляет `last_event_id`,
`event_tracking_required`, FK, индекс, CHECK и PostgreSQL trigger. Новая или
изменённая tracked-запись без нового signed event отклоняется SQLSTATE `23514`.
Существующие адреса помечаются legacy-untracked; первая обычная команда переводит
их в tracked-состояние. Demo seed не перезаписывает уже отслеживаемые изменения.

Объединение дубликатов также считается изменением адреса: UUID события
identity.duplicate_merge_decided резервируется до переноса, записывается в
last_event_id и переводит legacy-адрес под обязательное отслеживание.

## Ответственность и приватность

События `identity.participant_address_created`,
`identity.participant_address_updated` и
`identity.participant_address_archived` входят в critical registry.
`critical-command-assurance-v2` сохраняет участника, активную постоянную роль,
членство, idempotency basis, custody exposure и следующего ответственного.

Полный адрес, имя контакта, телефон, инструкции и пользовательская метка не
попадают в immutable journal. Signed payload содержит только member/cooperative,
назначение, регион, статус, версию, признаки default и идентификаторы затронутых
default-записей. Полные контактные данные остаются в приватной operational table.

## Проверки

- integration flow проходит create/replay/update/stale conflict/privacy/archive;
- искусственное падение audit откатывает state, event, signature, outbox и
  idempotency;
- команда принимается без worker, а outbox остаётся доставляемым асинхронно;
- прямой SQLAlchemy UPDATE без нового event отклоняется PostgreSQL trigger;
- AST registry требует assurance на всех трёх event types;
- member merge сохраняет UUID подписанного решения в перенесённом адресе;
- journal crash/restart/concurrency regression и `alembic check` проходят;
- migration drill проверяет `0038 -> 0039 -> 0038 -> 0039` и неизменность
  count/last hash принятого журнала.

Это инженерная гарантия программного контура. Она не заменяет правила хранения
персональных данных, утверждённые кооперативом, и внешний privacy/security review.