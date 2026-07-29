# Развёртывание

Статус: реализованный deployment baseline Slices 0-28; до выполнения `production_readiness.md` не является production-ready.

## Целевая топология локального узла

Один физический или виртуальный Linux host. Каждый signed bundle v2
квалифицирует ровно одну платформу: `linux/amd64` либо `linux/arm64`, а вторую
явно исключает. ARM64 допускается только после полного release gate на ARM64;
обычная AMD64-сборка не считается доказательством. Резервное устройство должно
совпадать с платформой bundle и принять тот же offline bundle и backup.

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

Для service-client network allowlist source IP берётся только из доверенной
proxy-границы. `api` не публикует host port и принимает HTTP только из internal
`app` network; `gateway` обязан удалить входные forwarded headers и записать
фактический адрес непосредственного сетевого peer. За внешним TLS reverse proxy allowlist видит адрес этого proxy; доверять исходному адресу клиента можно только после отдельного security review и настройки точного trusted real-IP профиля. Нельзя добавлять прямой `ports:` к `api` или ставить
перед gateway proxy, которому разрешено передавать произвольный
`X-Forwarded-For`, без отдельного security review. После изменения ingress
выполняется positive/negative проверка с адресом внутри и вне allowlist.

PostgreSQL и зашифрованный blob store являются обязательными частями хозяйственного состояния Slice 3. Метаданные и связи находятся в БД, содержимое доказательств находится в именованном `blob-data` volume. Согласованные backup/restore drill и контролируемые update/rollback реализованы операторскими scripts вне Compose. TLS lifecycle, непрерывный WAL archive и выпуск подписанного offline release bundle остаются обязательными задачами до pilot.

## Первый запуск одной командой

Сначала создаются файловые секреты и несекретный `.env`, затем Compose сам выполняет зависимости `postgres -> migrate -> init-node -> bootstrap-identity -> seed-demo -> api/worker -> gateway` и проверяет готовность.

Windows, из проводника или `cmd.exe`:

```bat
start.bat
```

Linux:

```bash
sh ./start.sh
```

Повторный запуск безопасен: существующие данные и изменённые пользователями пароли не перезаписываются. Адрес интерфейса по умолчанию: `http://127.0.0.1:8080`.

## Чистый production-запуск

Слово `production` означает запуск только из заранее собранного и подписанного offline bundle. Нужны Python 3 для независимого verifier, сам bundle, отдельно полученный public key, ожидаемый release id и утверждённый SHA-256 файла license policy.

Windows:

```bat
start.bat production <bundle-directory> <public-key> <release> <policy-sha256>
```

Linux:

```bash
sh ./start.sh production <bundle-directory> <public-key> <release> <policy-sha256>
```

Команда проверяет подпись, checksum inventory, SBOM, license policy и content ID каждого образа, загружает образы и только затем запускает Compose с `--no-build --pull never`. `COOP_ENVIRONMENT=production`, release, абсолютные пути проверенного bundle/public key и утверждённый policy hash сохраняются в `.env`; начальные пароли генерируются случайно в `secrets/bootstrap_*_password`; `seed-demo` не запускается.

Production запрещено включать поверх демонстрационной установки. `.env` с `COOP_DEMO_DATA_ENABLED=true`, известные demo bootstrap credentials или PostgreSQL-профиль с `demo_data_loaded=true` дают отказ. Для production создаётся чистый каталог и чистые volumes; из прежнего узла восстанавливается только отдельно проверенный production backup по recovery runbook. Нельзя менять флаг или удалять marker ради обхода отказа.

## Демонстрационные учетные записи

Эти данные действуют только для новой установки, впервые запущенной обычной командой `start.bat` или `sh ./start.sh`.

| Назначение | Логин | Начальный пароль |
|---|---|---|
| Регистрация участников | `registrar` | `CoopDemo-Registrar-2026!` |
| Учетные записи и права | `security` | `CoopDemo-Security-2026!` |
| Независимое одобрение | `auditor` | `CoopDemo-Auditor-2026!` |
| Обычный пайщик для проверки рынка | `farmer` | `CoopDemo-Farmer-2026!` |

Операторские учётные записи при первом входе требуют заменить начальный пароль. После замены пароль из таблицы больше не действует и повторный запуск его не восстанавливает. Файлы `secrets/bootstrap_*_password` также содержат только первоначальные значения, а не новый пароль пользователя. Учётная запись `farmer` создаётся только при включённых демоданных, сразу открывает пользовательский кабинет и не требует смены пароля, чтобы учебный путь можно было повторять.

### Demo accounts

These credentials apply only to a fresh installation first started with `start.bat` or `sh ./start.sh`.

| Purpose | Login | Initial password |
|---|---|---|
| Member registration | `registrar` | `CoopDemo-Registrar-2026!` |
| Accounts and permissions | `security` | `CoopDemo-Security-2026!` |
| Independent approval | `auditor` | `CoopDemo-Auditor-2026!` |
| Ordinary member for market verification | `farmer` | `CoopDemo-Farmer-2026!` |

Operator accounts require a password change on first sign-in. Once changed, the password in this table no longer works and restarting the stack does not restore it. The `farmer` account exists only with demo data enabled, opens the member workspace immediately, and deliberately keeps its reusable training password.

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

Manifest содержит version, git commit, build id, qualified/excluded platforms,
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

Отдельный `mfa_encryption_key` создаётся тем же bootstrap-скриптом как
независимое 32-байтовое hex-значение. Compose монтирует его только backend
runtime/migration jobs через `/run/secrets/mfa_encryption_key`. Файл нельзя
копировать в release, image или обычный `.env`. Он должен входить только в
зашифрованный recovery material: без него существующие TOTP seeds нельзя
расшифровать, поэтому восстановление потребует двойного контроля, сброса MFA и
повторного подключения пользователями.
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

Текущая schema head: `0024_marketplace_scope`. Revisions
`0015_federated_discovery`, `0016_peer_protocol`, `0017_peer_reservations`,
`0018_inter_node_clearing`, `0019_exchange_participant`,
`0020_purchase_deal_bridge`, `0021_logistics_contacts`,
`0022_participant_addresses`, `0023_antifraud_controls` и
`0024_marketplace_scope` защищают подписанные offers/indexes/quotes, peer
exchanges, home-node holds, межузловой clearing, связь подтверждённого обмена с
локальной сделкой, приватные снимки адресов и личную адресную книгу,
неизменяемые основания проверки аномалий и явную принадлежность рыночных
записей кооперативу.

Revision `0024_marketplace_scope` backfill сначала использует организацию
подписавшей роли, затем единственное активное членство участника и только затем
кооператив локального node code. Остаток без однозначного владельца останавливает
upgrade. После миграции оператор обязан проверить отсутствие `NULL` в
`federated_offers.cooperative_id`, `logistics_quotes.cooperative_id` и
`purchase_intents.cooperative_id`.

Downgrade непустого federation или antifraud-контура запрещён. Revision
`0013_offline_nodes` отдельно защищает node contracts, epochs, packages,
incidents, key rotations и назначения federation-ролей; ограничения
`0012_crisis_reserves` и предыдущих срезов сохраняются. Перед обновлением и
после восстановления оператор проверяет journal, открытые antifraud holds,
active/due mandates, allocations/forms, reserve snapshot age, дела и апелляции.
## Реализованные операции Slice 11

Перед backup API и worker должны быть запущены. Скрипт проверяет signed journal
и secret storage, останавливает по container id только эти writers, создаёт
backup v2 для DB/blobs, redacted secret-audit, manifest и checksums, затем
возвращает те же containers в работу:

```bash
bash ./scripts/backup-node.sh /var/lib/cooperative-clearing/backups
bash ./scripts/verify-backup.sh /var/lib/cooperative-clearing/backups/<backup-id>
```

В PowerShell используются `backup-node.ps1` и `verify-backup.ps1`. Verifier
раскрывает blob archive, повторно сканирует backup и запускает SQL-контракт
после восстановления. Restore требует точного backup id и подтверждения
установленного recovery material.
Update проверяет offline manifest signature/checksums и точную подписанную пару
source release/schema, создаёт pre-update backup, останавливает writers,
применяет upgrade и требует exact target revision. Rollback повторно проверяет
оба bundle, выполняет разрешённый contract-ом Alembic downgrade target image,
возвращает previous signed images и сравнивает journal sequence/hash. При любой
непроверяемой или небезопасной миграции пакет не должен объявлять source
transition; тогда используется координированный restore.

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

В `COOP_ENVIRONMENT=production` коллектор всегда отклоняет dirty worktree и не допускает override. Он не включает
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
SBOM, license inventory, pinned policy, signed secret-audit, checksum inventory
и Ed25519 manifest. Verifier независимо повторяет payload/image secret scan.
Закрытый release key хранится вне source tree и узла; открытый ключ и fingerprint
поступают отдельным доверенным каналом.

```bash
sh scripts/verify-release-bundle.sh <bundle> <public-key> <expected-release>
python3 scripts/release_bundle.py verify \
  --bundle <bundle> \
  --public-key <public-key> \
  --expected-release <release> \
  --expected-platform linux/amd64 \
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
readiness переживает нормальный restart writers. Начиная со Slice 44 update
также требует точный подписанный source release/schema transition. Ошибка до
успешного gate выполняет проверенный downgrade и возвращает previous signed
application release без изменения принятого journal checkpoint.

Если переход не объявляет безопасный downgrade или его проверка не проходит,
пакет не устанавливается; оператор выполняет destructive restore записанного
backup. Restore сначала проверяет и загружает exact signed
release, затем возвращает DB ACL/data и blobs, выполняет init/bootstrap и
заканчивается health/journal gates. Evidence:
[implemented_slice_16.md](implemented_slice_16.md).
## Локальный монитор узла и диагностика

`start.sh` и `start.bat` через bootstrap автоматически создают `.operations` и
идемпотентно запускают host probe раз в 60 секунд. Каталог монтируется в API
только для чтения. Для ручной проверки:

```sh
python scripts/operational_status.py probe --root .
python scripts/operational_status.py start-probe --root .
python scripts/operational_status.py stop-probe --root .
```


Параметры probe читаются из локального `.env`; непустая переменная процесса имеет приоритет. Для Linux production задайте `COOP_UPS_NAME=<имя устройства NUT>`. Если NUT не
используется, интеграционный слой может передать одно из фиксированных значений
`COOP_UPS_STATUS`: `ONLINE`, `ON_BATTERY`, `LOW_BATTERY`, `NOT_CONFIGURED` или
`UNKNOWN`. Произвольное значение отклоняется. Аналогично допустим только
фиксированный `COOP_HOST_CLOCK_STATUS`. Эти overrides не должны маскировать
неисправность: их источник и владелец фиксируются в host runbook.

После `backup-node.sh` или `backup-node.ps1` успешная завершённая копия
автоматически обновляет `.operations/backup-status.json`. Ручное редактирование
marker не считается evidence восстановления.

Зашифрованный пакет скачивается в GUI **Эксплуатация**. Для автономной
расшифровки:

```sh
python scripts/diagnostic_bundle.py \
  --input ./cooperative-clearing-diagnostic-YYYYMMDDTHHMMSSZ.ccdiag \
  --output-dir ./diagnostic-decoded \
  --passphrase-file ./diagnostic-passphrase.txt
```

Файл passphrase хранится и передаётся отдельно от `.ccdiag`; после работы его
удаляют по локальной secret-handling policy. Расшифрованный каталог считается
операционным материалом ограниченного доступа, даже несмотря на исключение PII
по контракту. Подробности: [implemented_slice_29.md](implemented_slice_29.md).

## Schema head 0039

Текущий application schema head:
`0039_participant_address_events`. Readiness отклоняет `0038` как устаревшую
схему. Перед перезапуском API/worker migration должна завершить address event FK,
CHECK, index и trigger; `alembic check` после обновления не должен обнаруживать
новых операций.

## Автономная проверка наблюдаемости

Перед пилотом и после изменения Docker/network policy выполните из корня узла:

```bash
bash ./scripts/test-local-observability.sh
```

или в Windows PowerShell:

```powershell
.\scripts\test-local-observability.ps1
```

Сценарий использует отдельный Compose project и порт, проверяет local
health/readiness, защищённые snapshot/metrics, bounded logs, четыре internal
сети и блокировку внешнего egress, затем удаляет тестовые volumes. Основной узел
на `127.0.0.1:8080` не останавливается. Каталог
`evidence/local-observability-<UTC>` должен содержать `PASSED` report и
проверяемый `SHA256SUMS`. Подробный контракт и ограничения:
[implemented_slice_46.md](implemented_slice_46.md).