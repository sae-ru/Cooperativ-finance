# Реализованный Slice 40: проверяемое отсутствие открытых секретов

Статус: acceptance criterion 126 реализован fail-closed на уровне исходников,
поставки, runtime-конфигурации, базы данных и резервной копии.

## Контракт

`scripts/supply_secret_audit.py` формирует только редактированный отчёт формата
`cooperative-clearing-secret-audit-v1`. Находка содержит правило, безопасный
путь и позицию, но никогда не значение секрета.

Проверяются:

- tracked и untracked source files, не исключённые Git;
- весь `node/` payload со строгим поиском секретных литералов;
- каждый файл каждого слоя четырёх Docker image archives;
- имена файлов закрытых ключей и runtime secrets;
- обычный `.env`: секрет допускается только как `*_FILE` со ссылкой на
  каталог secrets;
- PostgreSQL: Argon2id password hashes, 64-hex token/credential hashes,
  зашифрованные MFA factors, ожидаемый перечень secret-sensitive columns и
  отсутствие PEM, credential URL и secret JSON literals во всех
  text/JSON/bytea columns;
- backup dump как бинарный поток, распакованный blob archive, очищенный
  `runtime.env` и encrypted recovery material.

Публичные demo credentials не считаются production-секретами: аудитор считает
их отдельно, а production bootstrap всё равно запрещает demo values.

## Поставка

Release builder записывает `metadata/secret-audit.json`, включает его descriptor
и сводку в подписанный manifest. В отчёте обязательны шесть scopes:

- `source`;
- `node-payload`;
- `image:backend`;
- `image:frontend`;
- `image:gateway`;
- `image:postgres`.

Verifier не доверяет одному подписанному `PASSED`: после signature/checksum
verification он повторно сканирует `node/` и раскрывает все четыре image
archives. Результат обязан байт-в-байт совпасть со signed scope summary.

## База и backup

`infra/postgres/verify-secret-storage.sql` работает в read-only transaction и
возвращает единственную безопасную строку `secret_storage=PASS`. При нарушении
он сообщает только код, schema, table и column.

Backup format `cooperative-clearing-backup-v2` содержит checksummed:

- `secret-storage-verification.txt`;
- `backup-secret-audit.json`.

`verify-backup` повторно сканирует backup, восстанавливает его в одноразовый
PostgreSQL и снова запускает SQL-контракт уже на восстановленных данных.
Операционный статус не регистрирует backup без обоих чистых evidence-файлов.

## Проверки

- private PEM, AWS/GitHub/OpenAI token patterns, credential URL, secret filename,
  plaintext env и strict literal имеют отрицательные fixtures;
- подписанный ложный `PASSED` не скрывает private key внутри image layer;
- runtime production bootstrap отклоняет plaintext setting без вывода значения;
- живая БД проходит `secret_storage=PASS`;
- временная tampered DB с plaintext `users.password_hash` отклонена без утечки;
- реальный backup v2 восстановлен: schema `0037_actor_assurance`, 149 tables,
  434 signed events и 47 blob files.
- strict source scan: 735 files, 0 findings; полный script gate: 64 tests;
- backend regression: 256 passed, 1 acceptance deselected; затронутые frontend
  fixtures: 5 passed;
- signed `linux/amd64` bundle: 4 images, 48 node payload files, 6 secret scopes,
  independent rescan и `docker load` — `PASSED`.

Компактное evidence без OCI archives, private key и хозяйственных данных:
`evidence/secret-safety-20260728T185835Z`.

## Границы

Gate доказывает отсутствие высокоуверенных plaintext-secret patterns и
неверных форматов secret storage в проверяемых артефактах. Он не заменяет
ротацию уже опубликованного секрета, внешний security review, malware/DLP
экспертизу пользовательских вложений и production key ceremony.