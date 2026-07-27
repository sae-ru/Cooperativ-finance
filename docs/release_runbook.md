# Подписанный офлайн-релиз

Статус: реализованный эксплуатационный контракт Slice 15. Production-релиз
допускается только после полного [production readiness review](production_readiness.md).

## Состав релиза

Каталог релиза является замкнутым набором файлов:

- `release-manifest.json` с release id, commit, revision БД и версиями протоколов;
- `release-manifest.sig` с raw Ed25519-подписью точных байтов манифеста;
- `checksums.txt` с SHA-256 каждого файла, кроме самого списка;
- `images/*.oci.tar` с backend, frontend, gateway и PostgreSQL;
- `metadata/sbom/*.cdx.json` с CycloneDX 1.6 inventory;
- `metadata/licenses/*.json` с независимой классификацией лицензий;
- `metadata/license-policy.json` с версионированной политикой;
- `node/` с Compose, конфигурационным примером и эксплуатационными скриптами.

Verifier отклоняет симлинки, обход каталога, отсутствующие и лишние файлы,
несовпадающие размеры/hash, неверную подпись, release id, policy digest,
неполный набор ролей образов, изменённый content ID и запрещённую лицензию.
Загрузка образов начинается только после полной проверки всех файлов.

## Ключи релиза

Production-ключ создаётся на изолированном устройстве двумя назначенными
ответственными. Закрытый ключ:

- находится вне каталога исходного кода и узла;
- не включается в bundle, image, backup или evidence pack;
- на Linux имеет права `0600` или строже;
- используется только для утверждённого release manifest.

Открытый ключ и его SHA-256 fingerprint доставляются на узел отдельным
доверенным каналом. Ключ из того же носителя, что и непроверенный bundle, не
является независимым trust anchor.

Создание пары для тестового контура:

```bash
python3 scripts/release_bundle.py generate-keypair \
  --private-key /secure/release-private.pem \
  --public-key /secure/release-public.pem
```

## Сборка

Требуются Python 3.11+, Docker Engine, загруженные runtime-образы и отдельный
frontend test image для inventory production-зависимостей.

```bash
sh scripts/build-release-bundle.sh \
  1.0.0 \
  /release/cooperative-clearing-1.0.0 \
  /secure/release-private.pem \
  cooperative-clearing/frontend-test:1.0.0
```

Сборщик отказывается работать с dirty source tree. `--allow-dirty` разрешён
только для локального приёмочного теста и фиксирует все dirty entries в
подписанном манифесте.

## Независимая проверка

Сначала сверяется fingerprint открытого ключа с подписанным бумажным или
офлайн-реестром. Затем оператор фиксирует отдельно утверждённый hash license
policy:

```bash
export COOP_RELEASE_LICENSE_POLICY_SHA256=<approved-sha256>
sh scripts/verify-release-bundle.sh \
  /srv/cooperative-clearing/releases/1.0.0 \
  /etc/cooperative-clearing/release-public.pem \
  1.0.0
```

Для проверки и импорта образов одной fail-closed операцией:

```bash
python3 scripts/release_bundle.py verify \
  --bundle /srv/cooperative-clearing/releases/1.0.0 \
  --public-key /etc/cooperative-clearing/release-public.pem \
  --expected-release 1.0.0 \
  --expected-policy-sha256 <approved-sha256> \
  --load-images
```

`review_required` не означает автоматический запрет, но production release
нельзя утвердить, пока назначенный reviewer не сопоставил каждую такую запись
с первичным текстом лицензии. Любой `blocked > 0` verifier отклоняет всегда.

## Чистая установка без сети

1. На независимой машине сверить fingerprint public key и утверждённый SHA-256 license policy.
2. Скопировать содержимое подписанного `node/` в новый постоянный каталог узла, а полный release bundle и public key разместить по стабильным абсолютным путям; не использовать каталог прежней demo-установки.
3. Запустить единую fail-closed команду из нового каталога:

```bash
sh ./start.sh production \
  /srv/cooperative-clearing/releases/1.0.0 \
  /etc/cooperative-clearing/release-public.pem \
  1.0.0 \
  <approved-license-policy-sha256>
```

Windows использует `start.bat production` и те же четыре аргумента. Команда сама повторно проверяет manifest, signature, checksum inventory, SBOM, license policy и content ID, загружает образы, создаёт случайные секреты, записывает канонический `COOP_ENVIRONMENT=production` и выполняет `docker compose up -d --no-build --pull never`. Абсолютные пути bundle/public key и policy hash атомарно сохраняются в `.env` для backup/update; закрытие терминала их не теряет.

4. Выполнить вход назначенными ролями и `docker compose run --rm --no-deps api coopctl verify-journal`.
5. Зафиксировать release id, fingerprint, policy hash, image IDs и результаты в подписанном readiness-протоколе.

Если Compose пытается выполнить pull/build, verifier не совпадает с bundle, каталог содержит demo configuration/credentials или PostgreSQL сообщает `demo_data_loaded`, установка останавливается. Оператор не удаляет marker и не меняет environment ради обхода: для production создаётся чистый узел.

## Обновление

```bash
sh scripts/update-node.sh 1.0.1 /srv/cooperative-clearing/releases/1.0.1
```

`update-node` по умолчанию читает сохранённые public key и policy hash, вызывает тот же verifier, загружает только проверенные образы,
создаёт pre-update backup, применяет upgrade migration, проверяет health и
журнал, затем переключает сохранённый recovery context на новый bundle. Ошибка до успешного gate запускает application rollback; несовместимая
схема требует restore по recovery runbook.

## Приёмочные признаки

- verifier проходит с утверждёнными release, key fingerprint и policy hash;
- один изменённый байт, лишний файл, иной ключ или обход пути дают отказ;
- после `--load-images` content ID каждого reference равен манифесту;
- чистый узел стартует с `--pull never --no-build`;
- закрытый ключ отсутствует в bundle и удалён с release workstation;
- все `review_required` лицензии имеют отдельное подписанное решение.

## FULL backup и recovery release

Production update разрешён только если текущий signed release доступен через
`COOP_VERIFIED_RELEASE_BUNDLE`, а recovery material через
`COOP_ENCRYPTED_RECOVERY_BUNDLE`. Backup повторно проверяет release key,
expected version и pinned policy, затем включает bundle целиком.

`FULL` не определяется одним наличием secrets: без verified release копия
остаётся `DATA_ONLY`. Dump сохраняет точные GRANT/REVOKE runtime role; restore
воспроизводит ACL до запуска `init-node`, API и worker.

## API compatibility

До подписи release выполняется scripts/openapi_compat.py: текущий backend
contract сравнивается с принятым release baseline, а frontend mirror обязан
совпасть побайтово. Отчет входит в production evidence. Любой breaking или
неразрешимый contract change останавливает package job; обход допускается
только новой версией baseline и отдельным принятым решением о переходе.
