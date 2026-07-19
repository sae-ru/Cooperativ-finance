# Резервное копирование и восстановление

Статус: обязательный runbook; выполняется на полевых учениях.

## Цели

Инженерные цели до утверждения более строгой пилотной политики:

- RPO локального отказа: не более 15 минут;
- RPO потери основного узла и локального диска: не более 24 часов;
- RTO на подготовленном резервном оборудовании: не более 4 часов;
- проверка восстановления: минимум ежемесячно и перед каждым обновлением.

## Полный backup set

- PostgreSQL base backup и WAL до согласованной точки;
- encrypted blobs;
- public certificates, revocation lists и node passport;
- encrypted key containers или recovery references, но не открытые ключи;
- versioned configuration без plaintext secrets;
- release manifest и OCI images;
- backup manifest с hashes, counts, schema/protocol versions и timestamps;
- бумажный список независимых хранителей recovery material.

Backup БД без blobs или manifest не считается полным.

## Стратегия 3-2-1

- рабочие данные;
- локальная encrypted backup copy на отдельном накопителе;
- сменяемая offline encrypted copy вне основного помещения.

Минимум одна копия физически отключена. Один человек не должен одновременно
владеть backup media и всем recovery material.

## Расписание

- continuous/local WAL archive;
- ежедневный incremental или полный согласованный backup;
- ежедневная проверка manifest и выборочное чтение blobs;
- еженедельная offline copy;
- ежемесячный full restore на резервном устройстве;
- немедленный backup перед migration, key rotation и policy update.

## Процедура backup

1. Зафиксировать node, release, schema, protocol и event checkpoint.
2. Выполнить согласованный PostgreSQL backup.
3. Снять immutable snapshot blobs относительно checkpoint.
4. Включить certificates, config и release artifacts.
5. Построить manifest и hashes.
6. Зашифровать set отдельным backup key.
7. Проверить расшифрование manifest и случайную выборку объектов.
8. Записать audit event без секретов.
9. Перенести offline copy под двойным контролем.

## Восстановление на резервном узле

1. Объявить incident и закрыть запись на старом узле, если он доступен.
2. Получить чистое проверенное оборудование.
3. Проверить offline release signature и установить точную версию.
4. Проверить backup manifest до расшифрования payload.
5. Восстановить PostgreSQL в isolated network.
6. Восстановить blobs и проверить все referenced hashes.
7. Восстановить public trust data и keys по процедуре двойного контроля.
8. Запустить integrity verifier: migrations, constraints, event chain, balances,
   outbox/inbox, clearing proofs, blob references.
9. Сверить последний event checkpoint с бумажным и соседними узлами.
10. Выполнить role-based smoke tests в read-only mode.
11. Назначить новый node status или оформить continuity старого node id.
12. Открыть запись отдельным подписанным решением.

## Потерянный интервал

События после RPO восстанавливаются из подписанных receipts соседних узлов,
бумажных форм и локальных доказательств. Они не добавляются простым SQL. Каждая
операция проходит recovery import с указанием источника и независимым review.

## Проверка целостности

- все foreign keys и CHECK constraints;
- event sequence и hash chain;
- signatures и revocation timeline;
- balances против reservations и rights;
- obligations против fulfillments/clearing entries;
- share balances против exposures;
- каждый evidence ref имеет blob с правильным hash;
- outbox/inbox не создают повторный эффект;
- read models перестраиваются и сравниваются.

## Компрометация, а не отказ

При подозрении на взлом backup не разворачивается поверх старого host. Нужны
clean host, known-good release, определение времени компрометации, ротация
ключей, revoke package и проверка событий после safe checkpoint.

## Отчёт учения

Фиксируются фактические RPO/RTO, использованные люди и носители, failed steps,
отсутствующие blobs, расхождения, ручные решения и corrective actions. Учение
не считается успешным только по факту запуска UI.
