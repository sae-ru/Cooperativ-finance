# Реализованный Slice 44: подписанная совместимость и проверяемый rollback

Дата проверки: 2026-07-29.

Статус: критерий приёмки 130 закрыт инженерным контуром и изолированным
Docker-drill. Production-ключ, реальный recovery material и учение на целевом
оборудовании остаются внешними организационными gates.

## Подписанный контракт перехода

Bundle v2 теперь обязательно содержит
`cooperative-clearing-release-compatibility-v1`:

- точную target Alembic revision;
- стратегию `expand-contract`;
- версии peer, sync и federated-clearing протоколов;
- список точных пар `source release + source schema`;
- для каждой пары только `alembic-downgrade` и сохранение событий после
  резервной точки.

Пакет без контракта, с неизвестной версией manifest, неподдерживаемой парой,
повторяющимся переходом или иным rollback mode отклоняется до `docker image
load`, backup, изменения `.env` и migration. Release manager задаёт допустимые
источники повторяемым аргументом `--upgrade-from RELEASE@SCHEMA`. Пустой список
означает пакет только для чистой установки.

## Update

Linux и Windows scripts:

1. читают фактическую revision работающей БД;
2. повторно проверяют текущий signed bundle;
3. проверяют target signature, checksums, exact release, source release/schema,
   platform, image IDs, SBOM, licenses и secret audit;
4. создают согласованный pre-update backup;
5. для старого приложения запускают read-only consistency verifier из уже
   проверенного target backend, не переключая release;
6. останавливают API, worker, frontend и gateway;
7. выполняют upgrade и требуют точную target revision;
8. открывают сервисы только после readiness, journal и restore-consistency gates.

Operation state содержит обе версии, обе revision, оба абсолютных bundle path и
точный backup. Production запрещает build, DATA_ONLY backup и faultpoints.

## Rollback

Rollback не доверяет локальному image tag. Он повторно проверяет target
transition и previous bundle, загружает старые образы из подписанного пакета,
останавливает writers и фиксирует `last_sequence` и `last_event_hash`. Затем
target migration image выполняет downgrade до source revision. До переключения
приложения проверяются schema и полная согласованность состояния. После запуска
старых образов journal должен иметь ровно ту же последовательность и hash.

Успех записывает прежние release/schema и journal checkpoint в
`.operations/last-rollback.env`. Неожиданная schema, ошибка downgrade,
расхождение журнала или невозможность проверки оставляют операцию fail-closed и
ссылаются на pre-update backup; автоматический destructive restore не запускается.

## Автоматизированная проверка

- `26` unit tests release bundle проверяют подпись, manifest version, exact
  source transition и malformed contracts;
- script contract tests удерживают source/schema flags, dual-bundle rollback,
  consistency verifier release и journal checkpoint;
- migration gate выполняет `0037 -> 0038 -> 0037 -> 0038`, принимает события
  после upgrade и сравнивает их количество и последний hash после downgrade;
- Bash scripts проходят `bash -n`, PowerShell scripts проходят AST parse.

## Изолированный Docker-drill

Старый backend, frontend и gateway собраны из commit `48701b0`; новый backend
имеет отдельный content ID. Оба release прошли independent verification тестовым
Ed25519-ключом, `blocked licenses=0`, secret audit `PASSED`.

Отрицательные проверки:

- изменённая подпись с согласованным checksum была отклонена как invalid
  Ed25519 signature;
- корректно переподписанный manifest версии `3` был отклонён как unsupported;
- после обоих отказов узел оставался `s44-old@0037_actor_assurance` и READY.

Допустимый переход создал DATA_ONLY pre-update backup
`node-20260729T111137Z`, обновил узел до
`s44-new@0038_atomic_event_outbox` и сохранил 266 событий. После backup через
публичный API была принята сделка `4e99b1f9-e0ba-488d-870b-7c4cdc7086e3` с
подписанным событием `cba3eb56-feb7-45f5-ad55-8b36bfa2313d`.

Проверенный rollback вернул реальные старые образы и schema `0037`, сохранил
сделку в состоянии `PROPOSED`, journal sequence `267` и последний hash
`sha256:7d46a23a0f8ac837d3bf5fec56eb96108880adb413f94991f2c69b9a023eac91`.
Узел после отката READY. Drill использовал тестовый ключ и DATA_ONLY backup и не
выдаётся за production key ceremony или FULL recovery exercise.