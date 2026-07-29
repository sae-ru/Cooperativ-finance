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

## Исполняемая процедура Slice 11

Согласованный backup локального узла:

```bash
bash ./scripts/backup-node.sh /var/lib/cooperative-clearing/backups
```

Финальная строка stdout содержит абсолютный каталог завершённой копии. Формат
`cooperative-clearing-backup-v2` появляется только после успешных journal,
checksum, `pg_restore --list`, `tar -tzf`, redacted backup scan и
`secret_storage=PASS`. `manifest.env` различает:

- `FULL`: приложены отдельно зашифрованный recovery material и независимо
  проверенный signed release bundle точной версии;
- `DATA_ONLY`: отсутствует recovery material или release; такая копия не
  разрешает production update даже при целых БД и blobs.

Для FULL backup задаются `COOP_ENCRYPTED_RECOVERY_BUNDLE`,
`COOP_VERIFIED_RELEASE_BUNDLE`, `COOP_RELEASE_PUBLIC_KEY` и независимо
утверждённый `COOP_RELEASE_LICENSE_POLICY_SHA256`. Вложенный release повторно
проверяется при backup verification и до destructive restore.

Restore drill без воздействия на рабочий узел:

```bash
bash ./scripts/verify-backup.sh /var/lib/cooperative-clearing/backups/<backup-id>
```

Скрипт повторно сканирует dump/blobs/runtime, создаёт одноразовые PostgreSQL
container, network и volumes, восстанавливает dump и blobs, выполняет SQL
secret-storage contract на восстановленной БД, сверяет schema, таблицы, signed
events и число файлов, после чего удаляет временные ресурсы. Для Windows
operator workstation доступны `backup-node.ps1` и `verify-backup.ps1`.

Проверенное локальное учение Slice 40 восстановило backup v2 со schema
`0037_actor_assurance`, 149 таблицами, 434 signed events и 47 blob-файлами.
Tampered DB с plaintext `users.password_hash` была отклонена без вывода
значения.

Контролируемый restore является разрушительной операцией и требует двух явных
подтверждений:

```bash
COOP_RESTORE_CONFIRM=<backup-id> \
COOP_RECOVERY_CONFIRMED=yes \
bash ./scripts/restore-node.sh /path/to/<backup-id>
```

Перед production restore independently provisioned secrets должны соответствовать
recovery bundle. После восстановления обязательны migrations, node init,
identity bootstrap, signed journal verification и `verify-stack`. Автоматизация
не отменяет двойной контроль носителя и recovery material.

## Проверенное учение Slice 11

Копия `node-20260721T191032Z` прошла checksum/archive verification и независимый
restore drill: schema `0012_crisis_reserves`, 92 таблицы, 203 signed events и 24
blob-файла. Учение выполнялось до runtime upgrade на `0014`; оно доказывает
работоспособность data restore path, но не заменяет FULL restore на резервном
оборудовании с реальными recovery custodians и измерением RTO/RPO.
## Проверенное учение Slice 16

FULL backup включает exact release, PostgreSQL ACL, DB, blobs и encrypted
recovery material. Independent restore создаёт runtime role до replay ACL.
Update faultpoint после migration вернул previous release и healthy stack.
Последующий normal update и destructive restore из pre-update backup завершены;
runtime `coop_app`, init/bootstrap, health и signed journal проверены.

Локальное время полного restore составило 168,5 секунды. Оно не закрывает RTO
на резервном оборудовании и учение с реальными recovery custodians. Подробности:
[implemented_slice_16.md](implemented_slice_16.md).
## Проверенное учение Slice 44

Update/rollback теперь использует подписанный exact transition. Previous и
target bundle проверяются независимо; downgrade выполняется migration image
нового release до запуска старого application image. Journal sequence/hash
фиксируются при остановленных writers и обязаны совпасть после старта.

Изолированный переход `s44-old@0037 -> s44-new@0038 -> s44-old@0037` сохранил
сделку и signed event 267, принятые после pre-update backup. Неверная подпись и
корректно подписанный manifest неизвестной версии были отклонены до mutation.
При отказе verified rollback оператор не запускает автоматический restore, а
переходит к согласованному backup по этой инструкции.
## Восстановление доступа пользователя

Эта процедура не заменяет восстановление узла и не требует внешней почты, SMS
или OIDC.

1. Пользователь лично сообщает об утрате пароля/аутентификатора по утверждённому
   локальному каналу; сотрудник проверяет личность и оформляет акт.
2. Первый сотрудник с постоянной контрольной ролью входит в **Безопасность**,
   выполняет TOTP step-up, выбирает пользователя, задаёт одноразовый временный
   пароль, причину и номер акта.
3. Временный пароль передаётся пользователю по отдельному безопасному каналу и
   не вкладывается в акт, журнал или чат.
4. Другой персональный сотрудник независимо сверяет акт и пользователя, входит
   со своим TOTP и одобряет либо отклоняет pending recovery.
5. При одобрении сервер отзывает все старые сессии и TOTP пользователя. Старый
   пароль и прежний authenticator больше не работают.
6. Пользователь входит временным паролем, обязательно меняет его, открывает
   **Безопасность** и подключает новый TOTP через QR.
7. Первый сотрудник проверяет signed event/audit, а пользователь подтверждает,
   что новый вход работает. Акты хранятся по утверждённой retention policy.

Запрещено: одобрять собственную заявку, восстанавливать себе доступ, передавать
временный пароль второму контролёру, выполнять SQL reset или отключать проверку
step-up. При подозрении на компрометацию сначала изолируют узел/сессии и
сохраняют evidence, затем проводят recovery.

## Проверка согласованности Slice 43

Успех backup/restore теперь требует не только checksum архива и совпадения числа
файлов. `backup-node` в quiesced boundary создаёт `restore-consistency.json`,
который входит в `SHA256SUMS`. `verify-backup` восстанавливает DB и blobs в
одноразовые ресурсы и запускает `coopctl verify-restore-consistency` от UID
`10001` с установленными recovery secrets.

Команда полностью проверяет signed journal, соответствие активного Ed25519 key,
расшифрование всех TOTP factors и каждого `READY` evidence blob с контролем
AES-GCM, размера и plaintext SHA-256. В `restore-node` этот gate выполняется после
migrations/init/bootstrap и до запуска API, worker, frontend и gateway. Secret
values, TOTP seeds, содержимое и filesystem paths в JSON не выводятся.

Encrypted recovery bundle расшифровывают и устанавливают независимые хранители;
приложение не получает custody над recovery key, а доказывает совпадение уже
установленного материала с восстановленными данными.
