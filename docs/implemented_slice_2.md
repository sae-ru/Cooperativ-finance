# Реализованный Slice 2: подписанный журнал и персональная ответственность

Статус: реализовано и проверено на Linux-контейнерах с PostgreSQL. Срез создаёт
доказуемый контур критических команд, но сам по себе не разрешает реальные
расчёты, списание паёв или юридически значимое возмещение ущерба.

## Подписанный журнал

- хозяйственное состояние и append-only журнал хранятся раздельно;
- envelope канонизируется по профилю `RFC8785-JCS-1`;
- каждый event получает SHA-256 payload hash, previous hash и event hash;
- узел подписывает canonical envelope алгоритмом Ed25519;
- приватный seed читается только из Docker secret, в БД сохраняются публичный
  ключ, fingerprint, назначение ключа и срок действия;
- локальный sequence монотонен и сериализуется блокировкой строки головки
  цепочки;
- update/delete подписанных событий, подписей и consumer receipts запрещены
  триггерами PostgreSQL;
- verifier повторно строит canonical envelope, hash-chain и проверяет подпись
  каждого события.

## Атомарность и доставка

Критическая команда в одной транзакции PostgreSQL создаёт или изменяет state,
добавляет signed event, audit, outbox message и завершает idempotency record.
Ошибка любого шага откатывает весь набор.

Worker выбирает outbox через `FOR UPDATE SKIP LOCKED`, устанавливает lease,
повторяет временно неуспешную доставку с ограниченной задержкой и переводит
неисправимое сообщение в `QUARANTINED`. Локальный consumer фиксирует уникальный
receipt по паре event/consumer, поэтому повторная обработка безопасна.

Integration-тесты отдельно доказывают соседние sequence для конкурентных
команд, полный rollback при отказе audit и переход повреждённого сообщения
`retry -> quarantine`.

## Персональная ответственность

`ResponsibilityAssignment` связывает именованного участника, активную роль,
кооператив, конкретный объект, границы полномочий, предельный объём и срок.
Жизненный цикл:

```text
PENDING_APPROVAL -> PENDING_ACCEPTANCE -> ACTIVE -> RELEASED
                 -> REJECTED
```

- предложение создаёт физический оператор с допустимой scoped-ролью;
- решение принимает другой оператор с `RISK_ADMIN` или `AUDITOR`;
- создатель и назначаемый человек не могут одобрить собственное назначение;
- после одобрения назначаемый человек лично принимает ответственность;
- критические переходы используют optimistic version и `Idempotency-Key`;
- перед созданием GUI получает canonical summary и его hash; сервер отклоняет
  команду, если отправленный hash не соответствует фактическим полям;
- scoped-оператор видит только свои кооперативы и назначения, участник только
  собственные назначения, а node-level журнал доступен только глобальным
  контрольным ролям.

Назначение не является автоматическим признанием вины и не списывает пай.
Имущественное последствие появится только в Slice 6 как отдельное liability
case с доказанной причинной связью, пределом, независимым решением и апелляцией.

## API и интерфейс

Основные endpoints:

```text
GET  /api/v1/responsibility/candidates
GET  /api/v1/responsibility/assignments
GET  /api/v1/responsibility/approvals
POST /api/v1/responsibility/preview
POST /api/v1/responsibility/assignments
POST /api/v1/responsibility/assignments/{id}/decision
POST /api/v1/responsibility/assignments/{id}/accept

GET  /api/v1/journal/events
GET  /api/v1/journal/integrity
GET  /api/v1/journal/outbox
```

PWA содержит рабочее место назначения, independent approve/reject, личное
принятие, canonical evidence, состояние hash-chain, outbox и раскрываемый
canonical JSON подписанного события. Role-aware навигация и серверная
авторизация проверяются независимо.

## Миграция и демоданные

Revision `0003_journal_responsibility` создаёт схемы и ограничения journal/risk,
регистр публичных ключей, chain head, signed events, signatures, outbox,
consumer receipts, assignments и approvals. `alembic check` не обнаруживает
расхождения моделей и схемы.

Demo job через реальные команды создаёт одну цепочку
`proposal -> independent approval -> personal acceptance`. Повторный запуск
идемпотентен. Integration-тесты работают в отдельной БД
`cooperative_clearing_test` и не загрязняют журнал локального узла.

## Проверка

- backend: Ruff, strict mypy, 42 Pytest, coverage 80.93%;
- frontend: strict TypeScript, 18 Vitest, coverage 90.70%, production PWA build;
- PostgreSQL: migration upgrade и drift-check;
- Compose: health всех сервисов, worker heartbeat, gateway smoke;
- браузер: scoped risk flow, canonical preview, auditor journal, отсутствие
  console errors;
- runtime: целостность `OK`, outbox без pending и quarantine.

## Что ещё не готово

- эксплуатационная ротация и отзыв node signing key;
- внешний transport outbox и межузловой inbox;
- товарные партии, attestations, custody и evidence blobs;
- паевые резервы, liability case, причинная связь и апелляция;
- сделки, клиринг, федеративный поиск и межузловой протокол;
- production TLS, offline signed bundle и полный backup/restore drill.

Следующий production slice: Inventory vertical flow.
