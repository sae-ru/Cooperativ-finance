# Развёртывание

Статус: реализованный deployment baseline Slices 0-16; до выполнения `production_readiness.md` не является production-ready.

## Целевая топология локального узла

Один физический или виртуальный Linux host на x86-64; ARM64 допускается после
полного теста релиза. Резервное устройство способно принять тот же offline
bundle и backup.

## Сервисы Compose

| Service | Network exposure | Persistent data |
|---|---|---|
| `gateway` | host port, в production за LAN TLS termination | нет |
| `frontend` | только internal network | нет |
| `api` | только internal network | blob volume |
| `worker` | только data network | нет |
| `postgres` | только internal data network | database volume |
| `migrate`, `init-node`, `bootstrap-identity` | одноразовые jobs без внешнего порта | нет |
| `seed-demo` | только профиль `demo`, без внешнего порта | blob volume |
| `postgres-test`, `migrate-test`, `backend-tests` | только явный профиль `test`, отдельная test DB | изолированы от runtime DB |

PostgreSQL и зашифрованный blob store являются обязательными частями хозяйственного состояния Slice 3. Метаданные и связи находятся в БД, содержимое доказательств находится в именованном `blob-data` volume. Согласованные backup/restore drill и контролируемые update/rollback реализованы операторскими scripts вне Compose. TLS lifecycle, непрерывный WAL archive и выпуск подписанного offline release bundle остаются обязательными задачами до pilot.

## Реализованный первый запуск

Сначала создаются файловые секреты и несекретный `.env`, затем Compose сам выполняет зависимости `postgres -> migrate -> init-node -> bootstrap-identity -> api/worker -> gateway`.

```bash
sh ./scripts/bootstrap-node.sh
docker compose up -d --build
sh ./scripts/verify-stack.sh
```

На Windows для локальной разработки используются одноимённые `.ps1` scripts. `seed-demo` запускается только при активном профиле `demo`; защищённые environments отвергают демоданные при загрузке конфигурации.

## Каталоги host

```text
/opt/cooperative-clearing/releases/<version>/
/etc/cooperative-clearing/config/
/etc/cooperative-clearing/secrets/
/var/lib/cooperative-clearing/postgres/
/var/lib/cooperative-clearing/blobs/
/var/lib/cooperative-clearing/backups/
/var/log/cooperative-clearing/
```

Release directory read-only. Secrets и mutable data не находятся внутри
release directory.

## Offline bundle

```text
release-manifest.json
release-manifest.sig
images/*.oci.tar
compose.yaml
config-schema.json
migrations/
sbom/
checksums.txt
install/
upgrade/
rollback/
verify/
docs/
```

Manifest содержит version, git commit, build id, supported architectures,
image digests, DB schema range, protocol range и hashes всех файлов.

## Установка

1. Проверить аппаратные требования, время и свободное место.
2. Проверить release signing key из независимого доверенного источника.
3. Проверить manifest signature и checksums без сети.
4. Импортировать OCI images и сверить digests.
5. Создать runtime users, directories и permissions.
6. Сгенерировать node identity и secrets по утверждённой процедуре.
7. Применить config и TLS local CA.
8. Запустить PostgreSQL и readiness.
9. Применить migrations отдельной DB role.
10. Запустить API/worker/frontend/gateway.
11. Выполнить smoke test и подписать installation report.

## Хранение доказательств Slice 3

`api` и `seed-demo` монтируют `blob-data` в
`/var/lib/cooperative-clearing/blobs`. Файл содержит только AES-256-GCM
ciphertext; cooperative id и SHA-256 определяют content-addressed путь.
`worker`, `frontend` и `gateway` не имеют прямого доступа к volume.

Секрет `blob_encryption_key` создаётся bootstrap-скриптом отдельно от паролей
PostgreSQL и node signing seed. На production host права чтения выдаются только
runtime user контейнера. Ротация ключа требует отдельной процедуры decrypt,
проверки plaintext hash и re-encrypt; простая замена secret сделает старые
объекты недоступными.

Согласованный backup обязан фиксировать одну точку восстановления для:

1. PostgreSQL с таблицами `assets.evidence_blobs` и `assets.evidence_links`.
2. Полного `blob-data` volume.
3. Защищённой копии `blob_encryption_key` и требуемого signing material.

Backup без любого из трёх компонентов не является восстановимым. После restore
нужно выбрать каждый READY blob, расшифровать его, сверить размер и SHA-256, а
затем проверить signed journal. Согласованное копирование БД и blobs и изолированный restore drill автоматизированы в Slice 11. Полная проверка расшифрования каждого READY blob, отдельное хранение recovery material и регулярное расписание остаются обязательными production gates.

## Проверка журнала узла

После миграции, восстановления, перед открытием операционного дня и при
расследовании инцидента выполняется:

```bash
docker compose run --rm --no-deps api coopctl verify-journal
```

Команда проверяет локальную последовательность, previous hashes, canonical
payload, event hashes, срок действия ключей и Ed25519 signatures. Она печатает
один JSON-отчёт и возвращает exit code `0` только при `ok=true`. Нарушение
цепочки блокирует дальнейшие критические операции по runbook; автоматическое
исправление или удаление событий запрещено.

Текущая schema head: `0018_inter_node_clearing`. Revisions
`0015_federated_discovery`, `0016_peer_protocol`, `0017_peer_reservations` и
`0018_inter_node_clearing`
защищают подписанные offers/indexes/quotes, peer exchanges, purchase evidence и
home-node holds и межузловой clearing evidence. Downgrade непустого federation-контура запрещён. Revision
`0013_offline_nodes` отдельно защищает node contracts, epochs, packages,
incidents, key rotations и назначения federation-ролей; ограничения
`0012_crisis_reserves` и предыдущих срезов сохраняются. Проверка выполняется
внутри транзакции
Alembic до удаления данных. Ограничения предыдущих срезов сохраняются. Перед
обновлением и после восстановления оператор дополнительно проверяет active/due
mandates, открытые allocations/forms, reserve snapshot age, дела, апелляции и
неизменность signed journal.

## Реализованные операции Slice 11

Перед backup API и worker должны быть запущены. Скрипт проверяет signed journal,
останавливает по container id только эти writers, создаёт DB/blob backup, manifest
и checksums, затем возвращает те же containers в работу:

```bash
bash ./scripts/backup-node.sh /var/lib/cooperative-clearing/backups
bash ./scripts/verify-backup.sh /var/lib/cooperative-clearing/backups/<backup-id>
```

В PowerShell используются `backup-node.ps1` и `verify-backup.ps1`. Restore
требует точного backup id и подтверждения установленного recovery material.
Update проверяет offline manifest signature/checksums, создаёт pre-update backup,
применяет только upgrade migrations и запускает health/journal gates. Rollback
не выполняет Alembic downgrade: при несовместимой схеме используется
координированный restore.

`verify-stack` по умолчанию проверяет health, system status и worker heartbeat.
Проверка одноразового bootstrap-пароля включается только явным
`COOP_VERIFY_BOOTSTRAP_LOGIN=true` или `-VerifyBootstrapLogin`, поскольку после
первого входа пароль обязан быть изменён.
## Hardening host

- минимальная поддерживаемая ОС и security updates фиксируются release matrix;
- firewall разрешает только LAN HTTPS и административный канал;
- PostgreSQL не слушает LAN;
- containers работают non-root, capabilities удаляются;
- no-new-privileges и read-only filesystem применяются где возможно;
- removable media импортируется через quarantine workflow;
- системное время имеет локальный fallback и мониторинг drift;
- BIOS/boot и disk encryption включаются, если оборудование поддерживает;
- автоматический публичный update запрещён.

## Конфигурация

Config проходит schema validation до запуска. Неизвестное поле является
ошибкой. Экономические policies не хранятся только в env: они являются
версионированными подписанными записями приложения.

## Обновление

1. Проверить подпись и совместимость.
2. Выполнить полный backup и restore precheck.
3. Остановить новые critical commands, завершить active transactions.
4. Создать DB checkpoint и скопировать текущий release manifest.
5. Применить expand migration.
6. Запустить новый API в maintenance verification mode.
7. Проверить health, schema, event verification и critical smoke tests.
8. Открыть запись и наблюдать defined stabilization window.
9. Contract migration выполняется отдельным последующим релизом.

При ошибке используется документированный rollback. Если downgrade схемы
небезопасен, восстановление выполняется в новую БД из pre-upgrade backup с
контролируемым переносом событий, принятых после точки backup.

## Release environments

- `dev`: локальная разработка, synthetic data;
- `test`: CI integration и migration tests;
- `staging-node`: production-like offline host;
- `pilot`: реальные ограниченные данные;
- `production`: только после production readiness review.

Production data не копируются в dev/test без необратимой минимизации.

Профиль `test` использует отдельный service `postgres-test` и БД `cooperative_clearing_test`. Integration-тесты не подключаются к runtime `postgres` и не могут добавлять события в журнал локального узла.

## Definition of deployed

Сервис считается развёрнутым после API health, DB migration check, worker
heartbeat, blob read/write test, key signing test, backup target check, UI smoke
test и локального входа без Интернета. Состояние containers само по себе не
доказательство готовности.


## Bootstrap операторов Slice 1

Одноразовая job `bootstrap-identity` создает раздельные учетные записи `registrar`, `security` и `auditor` только при пустом реестре пользователей. Их временные пароли находятся соответственно в `secrets/bootstrap_registrar_password`, `secrets/bootstrap_security_password` и `secrets/bootstrap_auditor_password`. При первом входе интерфейс требует смену пароля; повторный запуск job существующие учетные данные не меняет. Подробности: [implemented_slice_1.md](implemented_slice_1.md).
## Capacity и release evidence

После штатной проверки стенда можно выполнить read-only smoke:

```bash
sh ./scripts/capacity-smoke.sh
```

PowerShell: `./scripts/capacity-smoke.ps1`. Порог и объём задаются аргументами
обёртки или `COOP_CAPACITY_*` в Linux. Production evidence создаётся отдельно:

```bash
sh ./scripts/collect-production-evidence.sh
```

В `COOP_ENVIRONMENT=prod` коллектор отклоняет dirty worktree. Он не включает
логи, raw PII или secrets, создаёт `COMPLETE` и `SHA256SUMS`; локальный каталог
`evidence/` не публикуется в Git.

## Трёхузловой deployment gate Slice 14

Перед включением federation clearing release обязан пройти изолированный gate:

```bash
sh ./scripts/test-federation.sh
```

Topology создаёт три API и три PostgreSQL на закрытой Docker network. Она не
использует runtime volumes основного узла. Ошибка сохраняет диагностические
логи до cleanup; `KEEP_FEDERATION_TEST_STACK=1` оставляет стенд для ручного
разбора. Production deployment дополнительно обязан проверить HTTPS/mTLS,
раздельные signing keys, trust contracts, лимиты и доступность recovery
контактов каждого узла.

## Подписанный офлайн-релиз Slice 15

Release bundle содержит четыре runtime-образа, установочный `node/` payload,
SBOM, license inventory, pinned policy, checksum inventory и Ed25519 manifest.
Закрытый release key хранится вне source tree и узла; открытый ключ и fingerprint
поступают отдельным доверенным каналом.

```bash
sh scripts/verify-release-bundle.sh <bundle> <public-key> <expected-release>
python3 scripts/release_bundle.py verify \
  --bundle <bundle> \
  --public-key <public-key> \
  --expected-release <release> \
  --expected-policy-sha256 <approved-sha256> \
  --load-images
```

После проверки содержимое `node/` переносится в постоянный каталог, secrets
создаются локально, а первый запуск выполняется только с
`docker compose up -d --no-build --pull never`. Полный порядок, license review
и update flow описаны в [release runbook](release_runbook.md).
## FULL recovery и update rollback Slice 16

`update-node` требует FULL pre-update backup, где одновременно присутствуют
encrypted recovery material и verified release текущей версии. Bounded
readiness переживает нормальный restart writers. Ошибка до успешного gate
возвращает previous application release; schema downgrade запрещён.

Если old application несовместимо с expanded schema, выполняется destructive
restore записанного backup. Restore сначала проверяет и загружает exact signed
release, затем возвращает DB ACL/data и blobs, выполняет init/bootstrap и
заканчивается health/journal gates. Evidence:
[implemented_slice_16.md](implemented_slice_16.md).