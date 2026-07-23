# Офлайн-протокол и синхронизация

Статус: production design до появления нескольких узлов.

## Два разных offline

1. Нет внешнего Интернета, локальный узел работает: все разрешённые локальные
   операции продолжаются.
2. Браузер не видит локальный узел: разрешены только drafts, не события.

Эти режимы нельзя смешивать в интерфейсе или протоколе.

## Offline epoch узла

Epoch открывается подписанной policy и содержит:

- `epoch_id`, node, start, optional expiry;
- базовый remote checkpoint;
- разрешённые типы операций;
- лимиты товаров, прав, кредита и гарантий;
- policy/protocol versions;
- emergency contacts и правила закрытия.

Операция вне лимита отклоняется или требует бумажного emergency mandate с
последующим отдельным review. Epoch не продлевается задним числом.

## Event identity

- `event_id`: глобально уникальный UUID;
- `(node_id, local_sequence)`: строгий порядок источника;
- `previous_event_hash`: целостность цепочки;
- `aggregate_version`: локальная optimistic concurrency;
- `epoch_id`: контекст автономной работы.

Timestamp не используется как единственный порядок событий.

## Формат sync package

```text
manifest.json
events.ndjson
certificates/
revocations/
blobs/<sha256>
proofs/
package.sig
```

Manifest содержит package id, source/target, created/expiry, protocol version,
sequence range, base checkpoint, counts, hashes каждого файла, compression и
required capabilities. Подпись покрывает canonical manifest; manifest покрывает
всё содержимое.

## Экспорт

1. Выбрать события после подтверждённого checkpoint.
2. Включить необходимые certificates/revocations и только нужные blobs.
3. Проверить отсутствие запрещённой PII для target.
4. Сформировать hashes и manifest.
5. Подписать node key.
6. Записать на чистый носитель и повторно проверить чтением.
7. Создать audit record экспорта.

## Импорт

1. Скопировать пакет в quarantine directory без исполнения содержимого.
2. Проверить limits архива, пути и размеры до распаковки.
3. Проверить package signature и доверие source node.
4. Проверить expiry, protocol, certificates и revocations.
5. Проверить hash каждого файла и event chain.
6. Проверить replay и sequence range.
7. Построить simulation plan без изменения хозяйственных таблиц.
8. Классифицировать конфликты.
9. Показать оператору effect summary.
10. Применить допустимые события транзакционными группами.
11. Создать receipt package и новый checkpoint.

## Классы конфликтов

| Класс | Пример | Автоматическое решение |
|---|---|---|
| Duplicate | тот же event id/hash | ignore как idempotent |
| Tampered duplicate | тот же id, другой hash | reject + incident |
| Referential gap | нет родительского события | hold до зависимости |
| Concurrent metadata | две безопасные заметки | merge по явному правилу |
| Competing reservation | один остаток использован дважды | conflict, freeze |
| Double redemption | право погашено на двух узлах | conflict, freeze, arbitration |
| Role/key invalid | действие после отзыва | reject/incident по policy |
| Policy mismatch | разные правила риска | reject или protocol fork |
| Custody conflict | два текущих хранителя | freeze + physical audit |
| Reputation divergence | разные source facts | сохранить факты, rebuild profile |

Last-write-wins запрещён для количества, прав, обязательств, паёв, custody,
санкций, помощи и crisis policy.

## Conflict case

Case хранит оба набора событий, affected objects, временную шкалу,
предварительный maximum exposure, временные freezes, назначенных независимых
людей, evidence и итоговое решение. Решение создаёт compensating events, не
удаляет проигравшую ветвь истории.

## Бумажные операции

Бумажная форма имеет serial, node, epoch, form type/version, participant ids,
поля операции, signatures и QR с минимальным reference. При вводе создаётся
`paper_operation_recorded`; оригинал сканируется, а дубли проверяются по serial.

## Протокольная совместимость

Узел объявляет supported protocol и event schema ranges. Unknown event может
быть сохранён, но не применён только если он помечен non-critical. Unknown
critical event или policy version блокирует package.

## Ограничения безопасности

- архивы защищаются от zip slip и decompression bombs;
- executable content не запускается;
- носитель считается недоверенным;
- blob filenames вычисляются по hash, не берутся из user input;
- импорт работает без network fetch;
- package receipt подписывается и хранится у обеих сторон.

## Статус реализации Slice 11

Протокол реализован как bounded offline epoch и детерминированный signed ZIP.
Поддержаны export, import quarantine/inbox, simulation, conflict decisions,
atomic apply и signed receipt. Проверяются protocol/contract compatibility,
node/key status, hashes, previous checkpoint, event allowlist и exposure.
Повторный package id не создаёт повторного хозяйственного эффекта.

Глобальная eventual consistency не обещается: каждый узел хранит собственный
checkpoint и явно видимый conflict/pending state. Связь может переноситься через
сменный носитель; локальные операции не зависят от доступности federation.
