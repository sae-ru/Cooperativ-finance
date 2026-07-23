# Реализованный Slice 11: автономный узел и контролируемая федерация

Статус: реализовано и проверено на Linux-контейнерах с PostgreSQL. Срез
закрывает автономную работу узла, подключение соседнего узла, ограниченную
межузловую ответственность, перенос подписанных пакетов через сменный носитель,
разрешение конфликтов без удаления истории, бумажные оригиналы, ротацию ключей
и базовые операции восстановления.

Срез не включает глобальный поиск предложений и межузловой клиринг. Эти функции
относятся к Slices 13 и 14. Принятие узла не означает безусловного доверия:
каждая связь ограничена двусторонним договором, лимитами, сроком и именованными
ответственными людьми.

## Подключение и ответственность узла

Жизненный цикл внешнего узла включает заявку, техническую проверку,
независимое решение аудитора и активацию trust contract. Паспорт узла содержит
идентификатор, публичный ключ, protocol version и технические параметры.
До активации обязательны пять персональных назначений:

- владелец отношений с узлом;
- технический хранитель;
- администратор безопасности;
- хозяйственный оператор;
- независимый аудитор.

Один человек не может одновременно выполнить несовместимые этапы. Trust
contract фиксирует разрешённые типы событий, срок, лимиты входящего и исходящего
риска, node bond и правила offline epochs. Exposure учитывается отдельно от
паёв людей и не может превысить активный договорный предел. Инцидент может
перевести узел в quarantine; revoke ключа и восстановление связи требуют
отдельных доказательств и подписанных событий.

## Offline epochs и синхронизация

Узел открывает ограниченный epoch с допустимыми типами событий, сроком и
контрагентом. Экспорт формирует детерминированный ZIP-пакет с canonical manifest,
событиями, evidence references, предыдущим checkpoint, SHA-256 и подписью
Ed25519. Импорт проходит этапы:

1. проверка контейнера, manifest, hashes, подписи, ключа и срока;
2. помещение в inbox без хозяйственного эффекта;
3. simulation с классификацией конфликтов и расчётом ожидаемого exposure;
4. независимое решение по конфликтам;
5. атомарное apply разрешённого набора с защитой от повторного эффекта;
6. подписанная квитанция с новым checkpoint.

Повторный импорт того же package id идемпотентен. Расхождение истории,
неразрешённый тип события, превышение лимита, отозванный ключ или открытый
конфликт блокируют применение. Исправление выполняется новым компенсирующим
пакетом; импортированная история не редактируется.

## Бумажные оригиналы

`FederationPaperForm` связывается с открытым epoch и содержит уникальные для
узла serial и QR reference, checksum, тип, версию, участников, ограничения и
canonical payload hash. Поддержаны передача товара, логистическая передача,
приёмка услуги, аварийное действие узла и исключение.

Форму выдаёт registrar или хозяйственный оператор. Ввод оригинала выполняет
другой человек с ролью аудитора или администратора безопасности и только при
наличии подписей участников и evidence id. Независимый registrar/auditor может
аннулировать неиспользованный оригинал с причиной и доказательством. Epoch нельзя
закрыть, пока остаётся форма в состоянии `ISSUED`.

Issued/recorded/void evidence и actor references защищены PostgreSQL trigger;
DELETE запрещён runtime-роли. Для синхронизации используются события
`federation.paper_form_issued`, `federation.paper_operation_recorded` и
`federation.paper_form_voided`.

## Ключи и инциденты

Ротация ключа содержит доказательство непрерывности: новый публичный ключ,
подпись запроса старым ключом и proof-of-possession нового ключа. Запрос делает
security administrator, решение принимает независимый auditor. Старый ключ
остаётся доступен для проверки исторических подписей. Revoke и quarantine не
переписывают уже принятые события, но блокируют новые пакеты по timeline.

## Эксплуатационные команды

Реализованы одинаковые Linux и PowerShell entry points:

| Операция | Linux | PowerShell |
|---|---|---|
| согласованный backup | `scripts/backup-node.sh` | `scripts/backup-node.ps1` |
| изолированный restore drill | `scripts/verify-backup.sh` | `scripts/verify-backup.ps1` |
| контролируемое восстановление | `scripts/restore-node.sh` | `scripts/restore-node.ps1` |
| обновление | `scripts/update-node.sh` | `scripts/update-node.ps1` |
| application rollback | `scripts/rollback-node.sh` | `scripts/rollback-node.ps1` |
| smoke/readiness | `scripts/verify-stack.sh` | `scripts/verify-stack.ps1` |

Backup останавливает только writers после успешной проверки signed journal,
создаёт custom-format PostgreSQL dump и архив encrypted blob volume, записывает
schema, manifest и `SHA256SUMS`, проверяет читаемость архивов и возвращает те же
контейнеры в работу. Копия имеет класс `FULL`, только если к ней приложен
зашифрованный recovery bundle; иначе это `DATA_ONLY`, требующий отдельно
сохранённых секретов.

Restore требует точного backup id и явного подтверждения установленного
recovery material. `verify-backup` восстанавливает БД и blobs в одноразовые
network/volumes, сверяет schema, таблицы, signed events и число файлов, затем
удаляет временные ресурсы. Update проверяет подпись offline release manifest,
checksums и OCI images, обязательно создаёт pre-update backup и запускает
migration/health/journal gates. Rollback меняет только приложение и никогда не
выполняет небезопасный Alembic downgrade; при несовместимости схемы оператор
восстанавливает согласованную pre-update копию.

## Хранение и миграции

Revision `0013_offline_nodes` создаёт federation node registry, responsibility
assignments, technical challenges, audit decisions, trust contracts, limits,
bonds, exposure ledger, incidents, key rotations, offline epochs, packages,
conflicts и receipts. Revision `0014_federation_paper_forms` добавляет бумажный
контур и database-level immutability. Downgrade каждой ревизии отказывается
удалять непустой хозяйственный контур.

Текущий schema head: `0014_federation_paper_forms`. Readiness требует именно
эту ревизию; `alembic check` подтверждает отсутствие незаписанных изменений
ORM-моделей.

## API и GUI

OpenAPI содержит 226 paths, из них 50 относятся к `/api/v1/federation`.
Backend и frontend specs имеют одинаковый SHA-256:
`25771D6D04143679EAF2D63EC12EACE4BE2FF7B597B948389BADAE4F500CBEE4`.
Все изменяющие команды требуют `Idempotency-Key`, а решения проверяют scope,
actor separation, version и актуальный contract.

Рабочее место «Федерация» содержит регистрацию и аудит узла, договоры и лимиты,
offline epochs, export/import/simulation/conflicts/apply, бумажные формы,
инциденты, quarantine/revoke и очередь ротации ключей. Команды отображаются по
роли, но backend повторно проверяет каждое полномочие.

## Демоданные

Идемпотентный сценарий создаёт проверенный внешний узел, пять персональных
назначений, активный trust contract, лимиты и bond, открывает offline epoch,
формирует и принимает подписанный пакет, разрешает conflict workflow и создаёт
receipt. Бумажная форма `DEMO-FED-PAPER-001` выдаётся хозяйственным оператором и
независимо вводится аудитором с подписями и evidence.

## Проверка

- backend: 123 Pytest, coverage 78,36% при пороге 75%;
- backend: Ruff clean, 388 Python-файлов отформатированы, strict mypy clean по
  164 source files;
- frontend: 39 Vitest files, 98 tests, production PWA build и strict TypeScript;
- frontend coverage: 82,89% statements, 70,00% branches, 77,04% functions,
  89,19% lines;
- migrations: clean upgrade `0001 -> 0014`, PostgreSQL constraints/triggers и
  `alembic check` без drift;
- backup: согласованная `DATA_ONLY` копия прошла checksum и archive verification;
- restore drill: `PASS`, schema `0012_crisis_reserves`, 92 таблицы, 203 signed
  events и 24 blob-файла восстановлены в изолированные volumes без изменения
  рабочего стенда;
- shell scripts прошли `bash -n`, PowerShell scripts прошли parser validation.

## Production-like Docker-стенд

- runtime migration `0012_crisis_reserves -> 0013_offline_nodes ->
  0014_federation_paper_forms` выполнена штатной migrate job;
- актуальные non-root API, worker, frontend и gateway images пересозданы без
  bind mounts; `verify-stack.ps1` подтвердил health, operational status и worker;
- runtime PostgreSQL содержит один demo external node, один trust contract, один
  epoch, два sync packages и одну paper form `RECORDED`;
- deployed `coopctl verify-journal` проверил 230 событий до sequence 230,
  failures отсутствуют, последний hash:
  `sha256:a9a8e19071dca1093e5759646a1f0fe4d187cce69bc9b2e5e2ea8667dc059759`;
- повторный runtime seed сохранил те же 230 событий и тот же последний hash;
- browser smoke на `1440x900` и DOM viewport `375x844` подтвердил mandatory
  first-login password gate без document-level overflow и без console
  warning/error. Защищённое federation workspace не открывалось обходом gate:
  операторский пароль намеренно не менялся автоматически; его layout и команды
  покрыты 98 frontend tests и production build.

Стенд доступен через <http://127.0.0.1:8080>. Эти доказательства закрывают
инженерный Slice 11, но не заменяют FULL restore на резервном host,
независимый security/legal review и пилотные критерии Slice 12.
