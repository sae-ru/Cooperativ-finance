# Развёртывание

Статус: production deployment baseline.

## Целевая топология локального узла

Один физический или виртуальный Linux host на x86-64; ARM64 допускается после
полного теста релиза. Резервное устройство способно принять тот же offline
bundle и backup.

## Сервисы Compose

| Service | Network exposure | Persistent data |
|---|---|---|
| `nginx` | LAN HTTPS | local certificates |
| `frontend` | только через nginx или static volume | нет |
| `api` | internal | нет |
| `worker` | internal | нет |
| `postgres` | internal only | database volume |
| `backup` | internal + mounted backup target | encrypted backup cache |

Blob store в MVP является host volume, доступным только API/worker/backup.
MinIO не является обязательным.

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
10. Запустить API/worker/frontend/nginx.
11. Выполнить smoke test и подписать installation report.

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

## Definition of deployed

Сервис считается развёрнутым после API health, DB migration check, worker
heartbeat, blob read/write test, key signing test, backup target check, UI smoke
test и локального входа без Интернета. Состояние containers само по себе не
доказательство готовности.
