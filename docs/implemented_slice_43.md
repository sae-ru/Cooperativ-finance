# Реализованный Slice 43: проверяемое полное восстановление

## Закрываемый критерий

Критерий 129 требует, чтобы восстановление из полной резервной копии возвращало
согласованные базу, подписанный журнал, установленный ключевой материал и все
связанные вложения с проверкой хешей.

## Исполняемый gate

Команда `coopctl verify-restore-consistency` работает только на чтение и
возвращает ограниченный JSON-отчёт без закрытых ключей, TOTP seeds, содержимого и
путей файлов. Она проверяет:

- всю локальную цепочку signed events, подписи, outbox и chain head;
- единственный активный `NODE_SIGNING` record против фактически установленного
  Ed25519 seed по fingerprint и public key;
- расшифрование каждого TOTP factor установленным MFA key;
- канонический storage key и алгоритм каждого `READY` evidence record;
- наличие, AES-256-GCM authentication, точный plaintext size и SHA-256 каждого
  связанного blob;
- отсутствие посторонних `.ccb` blobs, не представленных в восстановленной БД.

Максимум 100 отказов попадает в JSON, но `failure_count` считает все. Любое
расхождение завершает команду ненулевым exit code.

## Где gate исполняется

1. `backup-node` останавливает API и worker, затем проверяет согласованность в той
   же quiesced boundary, из которой создаются `database.dump` и `blobs.tar.gz`.
   Отчёт `restore-consistency.json` входит в `SHA256SUMS` и manifest.
2. `verify-backup` восстанавливает dump и blobs в одноразовые PostgreSQL,
   network и volumes, устанавливает переданные recovery secrets и запускает
   backend той же версии непривилегированным UID `10001`.
3. `restore-node` выполняет тот же gate после migrations/init/bootstrap и до
   запуска API, worker, frontend и gateway. Несовпадающие ключи или blobs не
   позволяют открыть восстановленный узел пользователям.

Encrypted recovery bundle остаётся непрозрачным для приложения. Его
расшифрование и установка выполняются независимыми хранителями; приложение
доказывает совпадение уже установленного материала с восстановленными данными.

## Тесты и живое доказательство

- Unit tests меняют ciphertext blob, добавляют orphan blob и подставляют другие
  node/MFA keys; verifier обязан отказать.
- PostgreSQL integration test портит реальный demo blob на один байт, получает
  `EVIDENCE_CONTENT_CORRUPT` и восстанавливает исходные байты в `finally`.
- Штатный backend regression: `261 passed, 2 deselected`, coverage `83.35%`;
  оба isolated-state acceptance drills запускаются отдельно.
- Живой узел: `434` journal events, `55/55` evidence records, `45` уникальных
  evidence blobs, `3/3` MFA factors, один совпадающий signing key, orphan `0`.
- Изолированный restore drill: schema `0038_atomic_event_outbox`, `149` таблиц,
  `434` events, `47` файлов архива; полный consistency report завершён `ok=true`.
- Синтетический FULL drill дополнительно проверил signed exact release из четырёх
  образов, encrypted recovery fixture и тот же восстановленный consistency
  report. Одноразовый private release key после проверки удалён.

DATA_ONLY и синтетический FULL drills проверяют software path. Production FULL
gate дополнительно требует production release key, реальный recovery material,
независимых хранителей и измерение RTO/RPO на резервном оборудовании.
