# Модель угроз

Статус: исходная threat model; обновляется на каждом значимом slice.

## Активы

1. Физические товары и доказательства их существования.
2. Товарные права, обязательства, паи и лимиты.
3. Подписанный журнал и clearing proofs.
4. Закрытые ключи и recovery material.
5. Персональные, медицинские и whistleblower данные.
6. Политики кризиса, санкций и распределения помощи.
7. Работоспособность локального узла и backups.

## Потенциальные нарушители

- внешний атакующий без учётной записи;
- обычный участник, превышающий полномочия;
- оператор или администратор;
- сговор двух обязательных ролей;
- контролёр, аудитор или арбитр с конфликтом интересов;
- захвативший устройство или съёмный носитель;
- скомпрометированный соседний узел;
- поставщик вредоносного обновления;
- лицо, оказывающее физическое давление на подписанта.

## Доверительные границы

Browser/API, API/PostgreSQL, API/blob store, worker/outbox, node/package,
operator/key store, backup/media, system/paper process.

## Ключевые attack paths

| Угроза | Контроль | Остаточный риск |
|---|---|---|
| выпуск сверх остатка | row lock, CHECK, reservation, unique, property test | сговор о ложном физическом остатке |
| двойное погашение | conditional transition, unique redemption, idempotency | компрометация нескольких ролей |
| подмена партии | custody chain, dual attestation, evidence hash | физическая фальсификация доказательств |
| переписывание журнала | append-only DB role, hash chain, external roots/backups | полный захват узла до публикации root |
| replay package | inbox unique, nonce, sequence, expiry | stolen active node key |
| кража ключа | encrypted storage, step-up, rotation, limits | coercion владельца активного ключа |
| admin escalation | separation of duties, audit, break-glass alert | сговор security admins |
| круговое поручительство | related-party graph, aggregate exposure | скрытые отношения вне известных данных |
| покупка репутации | donation excluded from formula | социальное давление вне системы |
| присвоение помощи | dual approval, delivery proof, recipient complaint | давление на получателя/свидетеля |
| ложная нуждаемость | minimal evidence, independent review, appeal | невозможность полной проверки в кризис |
| вредоносное обновление | release signature, SBOM, offline verify, rollback | компрометация release keys/build chain |
| ransomware | least privilege, offline immutable backup, restore drill | одновременная потеря ключей и backups |
| утечка PII | data separation, scopes, encrypted blobs, audit | screenshots и физический доступ |
| DoS локального узла | limits, local network, degraded runbook, paper forms | потеря энергии/оборудования |

## Abuse cases, обязательные для тестов

- один человек создаёт обе обязательные подписи через разные аккаунты;
- отозванная роль подтверждает pending approval;
- оператор меняет terms после первой подписи;
- повторный Idempotency-Key используется с другим payload;
- два workers одновременно применяют одну outbox запись;
- два клиента резервируют последний остаток;
- импорт содержит валидную подпись и несовместимую policy version;
- package пропускает часть node sequence;
- backup manifest валиден, но blob отсутствует;
- администратор пытается удалить signed event;
- донор получает повышенный score или приоритет помощи;
- арбитр рассматривает собственное первоначальное решение;
- protected share reserve попадает во взыскание;
- PWA показывает локальный draft как подтверждённую операцию.

## Физические угрозы

Software не подтверждает физический факт самостоятельно. Для критического
товара нужны независимые роли, выборочные сверки, меры/весы, custody transfer,
фото или акт, неожиданный аудит и процедура расхождения.

## Риски, которые нельзя устранить кодом

- юридическая неисполнимость договоров;
- недостоверная исходная оценка товара;
- массовый сговор сообщества;
- физическое насилие и захват склада;
- длительная потеря энергии и оборудования;
- дискриминационная политика, утверждённая организацией;
- отсутствие реальных резервов.

Для них требуются governance, аудит, внешние наблюдатели, бумажный контур и
условия остановки пилота.
