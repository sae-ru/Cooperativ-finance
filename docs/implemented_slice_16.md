# Реализованный Slice 16: FULL recovery и update rollback

Дата проверки: 2026-07-22.

Статус: инженерный контур backup/update/rollback/restore реализован и проверен
на отдельном Compose project с отдельными network и volumes. Это не заменяет
учение на физическом резервном host с назначенными recovery custodians.

## Изменения контракта backup

`FULL` теперь означает одновременное наличие:

- согласованного PostgreSQL dump;
- зашифрованного blob archive;
- encrypted recovery material;
- независимо проверенного signed release bundle точной версии;
- schema, runtime config без secrets, journal verification и checksum inventory.

Если recovery material или release отсутствует, копия остаётся `DATA_ONLY`.
Release сначала проходит Ed25519, expected release и pinned license policy
verification, затем целиком включается в backup. В `manifest.env` записываются
`release_material=included-verified` и hash release manifest.

PostgreSQL dump сохраняет ACL. Restore создаёт ожидаемую runtime role и
воспроизводит исходные GRANT/REVOKE, но не переносит owner. Это необходимо:
`--no-privileges` позволял восстановить данные, но оставлял `coop_app` без прав.

## Update и fault injection

`update-node` обеих платформ:

1. проверяет и загружает target release;
2. создаёт FULL pre-update backup текущего release;
3. фиксирует previous/target release и backup id;
4. переключает release;
5. выполняет только upgrade migration;
6. ждёт readiness с bounded retry;
7. проверяет signed journal;
8. при любой ошибке запускает application rollback.

Для автоматизированного drill доступны fail-closed точки
`after-release-switch`, `after-migration` и `after-startup` через
`COOP_UPDATE_FAILPOINT`. Неизвестное значение отклоняется до backup.

## Restore

До destructive-фазы restore:

- проверяет checksum backup;
- требует точный backup id и подтверждение recovery material;
- проверяет и загружает вложенный signed release;
- сверяет установленный Compose payload с recovery release;
- выбирает release из backup.

Затем приложения останавливаются, БД пересоздаётся, dump и blobs
восстанавливаются, migrations/init/bootstrap выполняются повторно, после чего
обязательны readiness retry и `coopctl verify-journal`.

## Найденные и исправленные дефекты

Живой drill обнаружил ошибки, не видимые parser/unit gate:

- `Path.GetRelativePath` отсутствует в старом Windows PowerShell;
- encoding `utf8NoBOM` там не поддерживается;
- health мог проверяться раньше восстановления API после backup;
- restore не инициализировал UTF-8 encoder;
- многострочный `sh -ec` искажался при передаче через Windows PowerShell;
- dump без ACL восстанавливал данные, но лишал runtime role прав.

Все ошибки были fail-closed. Последняя привела к остановке после import и до
запуска API, что позволило подтвердить и исправить permission recovery.

## Приёмка

### FULL backup основного демо-узла

Проверенная копия содержала 48 файлов и 559 057 070 байт. Independent drill:

```text
backup_kind=FULL
release=0.1.0-dev
schema=0018_inter_node_clearing
release_material=included-verified
restore_drill=PASS schema=0018_inter_node_clearing tables=130 events=242 blob_files=26
```

### Изолированный update/rollback

Узел `0.1.0-r1` работал на отдельном порту. Target `0.1.0-r2` использовал те же
immutable binaries, чтобы проверять только state machine операции.

Faultpoint после migration дал ожидаемый отказ. Автоматический rollback вернул
`COOP_RELEASE=0.1.0-r1`; node id сохранился, health и journal были валидны.
Повтор без faultpoint завершил update `r1 -> r2` и записал точный FULL backup.

ACL-сохраняющий backup отдельно восстановлен во временную PostgreSQL:
`130` таблиц и `26` blob-файлов. Затем destructive restore на update-стенде
проверил signed r1 release, пересоздал БД, восстановил ACL/data/blobs и вернул
r2 на r1. `init-node`, bootstrap, API, worker, frontend, gateway и journal
прошли. Полный restore занял 168,5 секунды на локальном Docker host; это не RTO
целевого оборудования.

Финальный тестовый `r4` bundle включает 30 signed installation files, в том
числе локальные deployment/recovery/security/readiness runbooks. Каждый файл
побайтно сверен с текущим деревом после independent verify.

## Открыто

- production key и реальный encrypted recovery material;
- измерение RPO/RTO на резервном оборудовании;
- migration между двумя действительно различными production schemas;
- power-loss/host-kill test вместо управляемого failpoint;
- независимый security review и учение с назначенными custodians.