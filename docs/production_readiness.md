# Production readiness

Статус: обязательный checklist перед реальными хозяйственными операциями.

## Governance and legal

- [ ] Пилотная юрисдикция и юридическая форма утверждены.
- [ ] Все open decisions, блокирующие feature, имеют подписанную policy.
- [ ] Паи, поручительство, protected amount и взыскание юридически проверены.
- [ ] Электронные и бумажные формы допустимы для своих операций.
- [ ] Privacy/retention и права субъектов утверждены.
- [ ] Appeal, external dispute и stop-pilot procedure утверждены.

## Domain safety

- [ ] Все инварианты ТЗ покрыты DB constraints и/или transaction tests.
- [ ] Нет float в количестве, оценке и покрытии.
- [ ] Партия прослеживается до права, исполнения и получателя.
- [ ] Нет двойного выпуска, погашения, резервирования и execution.
- [x] Protected amount и solidarity contour недоступны взысканию.
- [ ] Каждая critical command имеет actor/role/scope/evidence/exposure.
- [ ] Appeal и compensation проверены end-to-end.
- [x] Reserve status использует только physical verified evidence и bounded snapshot age ([evidence](implemented_slice_10.md)).
- [x] Crisis mandate имеет dual control, mandatory review, expiry, maximum end и safe state ([evidence](implemented_slice_10.md)).
- [x] Rationing сохраняет protected minimum, exact stock bound и не создаёт debt/reputation ([evidence](implemented_slice_10.md)).
- [x] Paper forms имеют unique serial/checksum/expiry, independent record и reconciliation ([evidence](implemented_slice_10.md)).
- [x] Anti-fraud signal сохраняет объяснимые факты/пороги, не принимает автоматическое обвинение и требует независимый evidence-backed review ([evidence](implemented_slice_19.md)).
- [x] Active anti-fraud HOLD исполняется в offer/quote/purchase/share-exposure командах и снимается только после `CLEARED` ([evidence](implemented_slice_19.md)).
- [x] Marketplace offer/quote/purchase имеют явного cooperative owner, включая команды global node roles ([evidence](implemented_slice_19.md)).
- [x] Все классы злоупотреблений раздела 24.5 имеют versioned rule и положительный/отрицательный синтетический regression-сценарий ([evidence](implemented_slice_21.md)).
- [ ] Для каждого антифрод-правила есть репрезентативный пилотный dataset, утверждённая частота ложных срабатываний и drift review.
- [x] Admin console разделяет User, Member, Membership, Organization и Node ([evidence](implemented_slice_22.md)).
- [x] Клиринговый cycle проходит freeze/preview/dispute/finalize/reconcile ([evidence](implemented_slice_7.md)).
- [x] Clearing proof и participant statements воспроизводимы ([evidence](implemented_slice_7.md)).
- [x] Active external node имеет owner, named roles и действующий trust contract ([evidence](implemented_slice_11.md)).
- [x] Bilateral node limits и node bond ограничивают внешнюю exposure ([evidence](implemented_slice_11.md)).
- [x] Quarantine, revoke и контролируемое восстановление узла проверены end-to-end ([evidence](implemented_slice_11.md)).
- [x] Federated offer search проверяет подпись, home node и freshness ([evidence](implemented_slice_13.md)).
- [x] Landed cost воспроизводима, а estimated logistics явно отделена ([evidence](implemented_slice_13.md)).
- [x] Goods/logistics reservation saga имеет expiry и компенсации ([evidence](implemented_slice_13.md)).
- [x] Inter-node prepare не превышает bilateral exposure ([evidence](implemented_slice_14.md)).
- [x] Commit certificate требует approvals всех affected home nodes ([evidence](implemented_slice_14.md)).
- [x] Pending local apply и reconciliation восстановлены после сбоя ([evidence](implemented_slice_14.md)).

## Security

- [ ] Threat model и independent security review завершены.
- [ ] Production keys сгенерированы и разделены по назначению.
- [ ] Private keys/secrets отсутствуют в Git/images/plain backup.
- [x] Local auth, revoke, TOTP step-up и scoped break-glass протестированы ([evidence](implemented_slice_20.md)); WebAuthn остаётся отдельным gate.
- [x] Release/package/event signatures имеют independent test vectors ([evidence](implemented_slice_15.md), [evidence](implemented_slice_11.md)).
- [ ] Critical/high findings закрыты или formal accepted risk подписан.
- [ ] Incident drill key compromise выполнен.

## Resilience

- [x] Узел устанавливается без Интернета и публичного registry ([evidence](implemented_slice_15.md)).
- [x] Production startup требует signed bundle, pinned policy и `--no-build --pull never`; demo/hardened transition fail-closed ([evidence](implemented_slice_28.md)).
- [ ] Полный restore на резервном оборудовании укладывается в RTO.
- [ ] RPO подтверждён измерением и сверкой событий.
- [x] FULL backup включает DB, blobs, manifest, trust data и verified release ([evidence](implemented_slice_16.md)).
- [x] Update, injected interrupted update, application rollback и FULL restore испытаны ([evidence](implemented_slice_16.md)).
- [x] Paper forms и последующий независимый ввод испытаны локально и в federation epoch ([evidence](implemented_slice_10.md), [evidence](implemented_slice_11.md)).
- [x] Offline export/import/simulation/conflict/apply drill завершён на integration-стенде ([evidence](implemented_slice_11.md)).
- [x] Federation не является обязательной runtime-зависимостью local critical path ([evidence](implemented_slice_11.md)).

## Quality

- [ ] CI release gates зелёные на конкретном commit.
- [ ] Migration с предыдущего production release проверена.
- [x] Clearing golden/property/permutation tests зелёные ([evidence](implemented_slice_17.md)).
- [x] Concurrency tests выполнены многократно ([evidence](implemented_slice_17.md)).
- [x] OpenAPI compatibility report принят как инженерный gate ([evidence](implemented_slice_17.md)).
- [ ] Browser/device/accessibility matrix пройдена.
- [ ] Capacity test выполнен на минимальном host.
- [ ] Нет flaky critical tests.

## Operations

- [ ] Назначены оператор, security admin, backup custodian и on-call contacts.
- [ ] Dashboards/alerts работают без внешнего Интернета (code-level baseline: [Slice 29](implemented_slice_29.md); target-host evidence не приложен).
- [ ] Runbooks доступны локально и на бумаге.
- [ ] Clock, disk, UPS и certificate monitoring работают (code-level baseline: [Slice 29](implemented_slice_29.md); реальный ИБП и target host не проверены).
- [ ] Support и escalation обучены без доступа к production secrets.
- [ ] Диагностический bundle проверен на отсутствие PII/secrets (bounded implementation: [Slice 29](implemented_slice_29.md); независимый privacy review не приложен).
- [ ] SBOM, licenses, image digests и release signature опубликованы локально.

## Pilot evidence

- [ ] Ручной процесс пройден до software rollout.
- [ ] Реальные роли выполнили training scenarios.
- [ ] Условия остановки и rollback пилота известны участникам.
- [ ] Метрики не создают скрытый social score.
- [ ] Независимый аудитор получил read-only evidence access.
- [ ] Первый restore и crisis drill назначены до запуска.

## Решение

Production readiness review создаёт подписанный протокол с release id, node,
списком evidence, открытыми residual risks, сроком следующего review и людьми,
принявшими решение. Checkbox без evidence link не считается выполненным.

## Текущее доказательство Slice 11

Code-level gates, миграции, signed package flow, role separation, paper forms и
изолированный restore drill пройдены. Это не переводит систему в production:
по-прежнему открыты юридические policies, независимый security review, FULL
restore с recovery custodians, измерение RTO/RPO, capacity/accessibility matrix,
подписанный offline release bundle и шестимесячный пилот. Checkbox внешнего
процесса нельзя закрыть результатом unit/integration-теста.
## Текущее доказательство Slice 12

Инженерный baseline observability, protected metrics/snapshot, capacity runner,
automated DOM accessibility checks и PII-free production evidence pack проверен.
Штатные gates: backend 129 тестов и 78,41% coverage, frontend 103 теста и 83,08%
statement coverage, OpenAPI 228 paths, deployed stack и signed journal зелёные.
Подробности: [implemented_slice_12.md](implemented_slice_12.md).

Локальный smoke `500` запросов, `424,258 RPS`, `p95=51,839 ms` не закрывает
capacity checkbox на минимальном целевом host. Automated DOM audit не закрывает
ручную browser/device/screen-reader matrix. Security review, legal review,
FULL restore с custodians и фактический шестимесячный pilot остаются внешними
обязательными критериями. Формы подписываемых решений находятся в
[evidence_templates](evidence_templates/production_readiness_decision.md).
## Текущее доказательство Slice 14

Межузловой prepare/commit/apply проверен на трёх независимых PostgreSQL, включая
недоступный при commit узел, same-certificate recovery и точную reconciliation.
Ruff, strict mypy, 158 backend tests с 75,16% coverage, 115 frontend tests,
typecheck/build и отдельный acceptance зелёные. Подробности:
[implemented_slice_14.md](implemented_slice_14.md).

Это закрывает только три отмеченных инженерных инварианта inter-node clearing.
Юридические, организационные, security, target-host и pilot checkbox остаются
открытыми и требуют внешних подписанных evidence.
## Текущее доказательство Slice 15

Signed offline bundle проверяет Ed25519 manifest, полный filesystem inventory,
content ID четырёх runtime-образов, SBOM и license policy до `docker load`.
Девять tamper tests зелёные. Чистый узел с пустыми volumes установлен и
проверен с `--pull never --no-build`; подробности:
[implemented_slice_15.md](implemented_slice_15.md).

Закрыты только два отмеченных инженерных checkbox. Использованный ключ был
одноразовым и удалён. Production key ceremony, ручное решение по 160
`review_required` лицензиям, remote CI на конкретном commit, update/rollback,
security review, target-host и pilot evidence остаются открытыми.
## Текущее доказательство Slice 16

FULL backup теперь требует и encrypted recovery material, и verified exact
release; PostgreSQL ACL, DB, blobs, schema и journal входят в проверяемый
контур. Independent restore и destructive restore завершены. Faultpoint после
migration автоматически вернул previous release, а повторный update прошёл.
Подробности: [implemented_slice_16.md](implemented_slice_16.md).

Закрыты только два отмеченных инженерных checkbox. RTO/RPO на резервном
оборудовании, power-loss drill, migration разных production schemas, production
keys/custodians и внешние security/legal/pilot evidence остаются открытыми.
## Текущее доказательство Slice 17

OpenAPI baseline и два snapshots совпадают для 298 операций. Триста
детерминированных clearing graphs прошли permutation/bounds/conservation.
Migration 0017 -> 0018, допустимый downgrade и re-upgrade сохранили node и
identity state. После исправления найденной зависимости теста от порядка данных
три полных concurrency rounds дали 21 + 21 + 21 passed; journal содержит 556
последовательных проверенных событий. Подробности:
[implemented_slice_17.md](implemented_slice_17.md).

Отмечены только три инженерных checkbox. Remote CI на конкретном commit,
фактический previous production release, длительная flaky history, target host,
manual accessibility и внешние security/legal/pilot evidence остаются
открытыми.
## Текущее доказательство Slice 19

Объяснимые anomaly signals, независимый evidence-backed review и active HOLD
проверены на PostgreSQL. Marketplace records имеют явного cooperative owner;
межкооперативная offer/quote/intent цепочка допустима, но каждый объект
проверяется в собственном scope. Populated migration cycle завершён без `NULL`
и orphan references. Ruff, strict mypy, 174 backend tests, отдельный
трёхузловой acceptance, 144 frontend tests, production build, OpenAPI
compatibility и живой RU/EN desktop/mobile light/dark browser smoke зелёные.
Подробности: [implemented_slice_19.md](implemented_slice_19.md).

Это закрывает только первый набор инженерных контролей. Полное versioned
покрытие раздела 24.5 добавлено в Slice 21; policy calibration на реальных
данных, privacy/security/legal review, manual accessibility matrix и пилотные
решения остаются открытыми.

## Текущее доказательство Slice 20

TOTP seed зашифрован отдельным node secret, server-side step-up имеет expiry,
replay и brute-force controls. Recovery и break-glass требуют двух разных
персональных сотрудников, сохраняют подписанные события, а аварийная роль имеет
отдельный source/scope/expiry и исчезает из действующей сессии после отзыва.
Backend, frontend и populated migration cycle проверены; подробности:
[implemented_slice_20.md](implemented_slice_20.md).

Это закрывает только инженерную проверку local auth/TOTP/break-glass. WebAuthn,
production key ceremony, независимый security review, физическое учение с
реальными сотрудниками и formal accepted risks остаются открытыми checkbox.
## Текущее доказательство Slice 21

Алгоритм `2.0.0` связывает каждый запуск с SHA-256 манифеста и версией
`synthetic-v2.0.0`. Все 13 классов раздела 24.5 представлены 15 объяснимыми
правилами; девять новых правил имеют положительные и отрицательные проверки.
Каталог API и RU/EN интерфейс показывают покрытие, реакцию и явный
`production_approved=false`. PostgreSQL integration подтверждает хранение
манифеста, signed event, реальный `HOLD` и независимое снятие ограничения.
Подробности: [implemented_slice_21.md](implemented_slice_21.md).

Этот результат закрывает инженерное versioned покрытие, но не пилотную
калибровку. Реальный размеченный dataset, утверждённый false-positive rate,
monitoring drift, privacy/legal review и независимое adversarial тестирование
остаются открытыми checkbox перед хозяйственным production.

## Текущее доказательство Slice 22

Организация, участник, membership, учётная запись и узел выведены в пять
отдельных административных реестров. Server-side cooperative scope применяется
к спискам, overview и командам. Status transitions сохраняют историю, а
отключение User атомарно отзывает активные сессии. Migration `0029`, generated
OpenAPI и проверки описаны в [implemented_slice_22.md](implemented_slice_22.md).

Отмечено только инженерное разделение сущностей. Массовый импорт и
service-client lifecycle впоследствии закрыты на code-level в Slice 23 и 24.
Юридические процедуры выхода, обучение реальных ролей, независимый security
review и юридически сложные transfer/succession cases остаются открытыми production gates.

## Текущее доказательство Slice 23

Безопасный ввод участников реализован как проверяемый workflow: ручное создание требует явного решения при совпадении имени, а массовый CSV проходит staging, dry run, независимое утверждение и повторную проверку перед атомарным применением. Сервер проверяет cooperative scope, постоянные роли, optimistic version и idempotency; открытые identifiers и исходный CSV не сохраняются.

Revision `0030_safe_member_intake`, OpenAPI, backend integration-тесты и
component-тесты интерфейса входят в кодовое доказательство. Service-client
lifecycle впоследствии закрыт на code-level в Slice 24. Процедура merge
подтверждённых дубликатов, независимый security review, обучение операторов,
целевая нагрузка и юридическое утверждение процедур остаются открытыми
production gates.

## Текущее доказательство Slice 24

Внешние программы отделены от человеческих учётных записей. Permanent manager
создаёт versioned request, другой permanent `SECURITY_ADMIN` с персональным
member и TOTP step-up принимает решение. Credentials выдаются один раз и
хранятся как hash; machine tokens revocable/source-bound, проверяют owner,
scope, network, expiry и PostgreSQL rate limit. Suspend/revoke не меняют human
sessions. Revision `0031`, OpenAPI, runtime cleanup, demo, API и RU/EN GUI
описаны в [implemented_slice_24.md](implemented_slice_24.md).

Checkpoint подтверждён `207` backend-тестами, `170` frontend-тестами, production build, Ruff, strict mypy по `205` source files, симметрией `774` RU/EN keys, циклом миграции `0031 -> 0030 -> 0031`, `alembic check`, Compose network audit и живым `OPERATIONAL` Docker-узлом. Браузерная проверка RU/EN и light/dark не выявила console errors.

Это code-level доказательство не закрывает независимый review proxy trust
boundary, secret storage внешней программы, фактические allowlists, incident
rotation drill, target-host capacity, обучение владельцев и юридическое
утверждение подключений. Эти checkbox требуют подписанных внешних evidence.
## Текущее доказательство Slice 25

Подтверждённые чистые дубликаты теперь проходят отдельный evidence case, независимое решение постоянного `SECURITY_ADMIN` с TOTP и повторную fail-closed проверку всех ссылок. Source не удаляется; `MERGED -> survivor` mapping, signed events, audit и idempotency сохраняют происхождение identity. Revision `0032`, OpenAPI, demo и RU/EN GUI описаны в [implemented_slice_25.md](implemented_slice_25.md).

Checkpoint Slice 25 подтверждён `215` backend-тестами, `173` frontend-тестами, production PWA build, Ruff, strict mypy по `208` source files, симметрией `846` RU/EN message keys и `396` system values, циклом миграции `0031 -> 0032 -> 0031 -> 0032`, `alembic check` и живым `OPERATIONAL` Docker-узлом на revision `0032_member_duplicate_merge`. В браузере проверены demo-case, RU/EN, light/dark и отсутствие console errors.

Code-level gate duplicate merge закрывается только для одной организации и карточки без хозяйственной истории. Реальные процедуры переноса паёв, долгов, поручительств, сделок, санкций, репутации, межкооперативной identity и наследования остаются внешними production gates. Перед production требуется независимый review миграции/pg_catalog scan, обучение операторов на blocked cases и подписанный регламент ошибочного объединения и восстановления.

## Текущее доказательство Slice 26

Процедура выхода и преемственности реализована как fail-closed containment: немедленное отключение доступов, versioned snapshot, независимое персональное решение постоянного `SECURITY_ADMIN` с TOTP, подписанные события и отсутствие универсального переноса экономических связей. Revision `0033`, OpenAPI, demo и RU/EN GUI описаны в [implemented_slice_26.md](implemented_slice_26.md).

Checkpoint подтверждён `235` backend-тестами (`1 deselected`), backend coverage `78.02%`, `176` frontend-тестами, production PWA build, Ruff, strict mypy по `253` source files, симметрией `923` RU/EN message keys и `401` system values, циклом миграции `0032 -> 0033 -> 0032 -> 0033`, `alembic check` и живым `OPERATIONAL` Docker-узлом на revision `0033_member_continuity`. В браузере проверены демодело, RU/EN, light/dark и отсутствие console errors.

Это доказательство не закрывает внешний legal gate: правила наследования паёв, долгов, поручительств, активных сделок и межузловых обязательств остаются обязательным условием реальной эксплуатации.

## Текущее доказательство Slice 27

Аварийная передача физического хранения привязана к подтверждённому делу
смерти или недееспособности. Партии немедленно удерживаются, независимый
контролёр прикладывает адресуемые по содержимому доказательства пересчёта,
постоянный `SECURITY_ADMIN` выполняет независимое решение с TOTP, а новый
постоянный хранитель принимает ответственность лично. До личной приёмки
старое назначение сохраняется; расхождение блокирует процесс и не исправляет
количество автоматически. Паи, долги, роли, репутация и право собственности
не наследуются этим процессом.

Checkpoint подтверждён `241` backend-тестом (`1 deselected`) и coverage
`77.80%`, `180` frontend-тестами в `65` файлах и coverage `82.00%` statements /
`70.97%` branches / `75.10%` functions / `87.84%` lines, production PWA build,
Ruff, strict mypy по `258` Python-файлам, побайтным равенством OpenAPI и
симметрией `1016` RU/EN message keys и `401` system values. Чистая схема прошла
цикл `0033 -> 0034 -> 0033 -> 0034` и `alembic check`. Живой Docker-узел имеет
статус `OPERATIONAL`, worker `RUNNING` и revision `0034_custody_continuity`.
В браузере проверены демодело, RU/EN, light/dark, локализация динамических
демозначений, контраст предупреждения о прежнем хранителе и отсутствие console
errors.

Это code-level доказательство не решает наследование имущества и не заменяет
юридически утверждённый регламент действий при смерти или недееспособности.
Перед реальной эксплуатацией остаются обязательными внешний legal review,
обучение персонала и независимое учение с физическим пересчётом и подписанными
актами.

## Текущее доказательство Slice 28

Аудит обнаружил несовпадение `production`/`prod`: прежний production wrapper оставлял runtime в `dev`, а update/evidence guards могли не включиться. Исправление вводит один executable environment contract, сохраняет режим в `.env`, требует signed offline bundle, independently obtained key, expected release и pinned license-policy hash, загружает только проверенные образы и запускает Compose с `--no-build --pull never`.

Demo configuration, известные demo credentials и PostgreSQL marker `demo_data_loaded=true` блокируют production. Отдельный DB guard запрещает обход wrapper через ручные Compose variables. Update и evidence scripts используют тот же canonical resolver; dirty override в production запрещён. Доказательства: [implemented_slice_28.md](implemented_slice_28.md).

Checkpoint: `244 passed, 1 deselected`, backend coverage `77.81%`, Ruff, strict mypy по `258` файлам, `29` script tests, shell/PowerShell syntax, `alembic check`, compatible exact-mirror OpenAPI и живой `OPERATIONAL` demo-узел после `start.bat demo`.

Закрыт только code-level deployment invariant. Подписанный readiness protocol, production key ceremony, назначенные реальные custodians, независимые security/legal reviews, target-host RTO/RPO/capacity, accessibility matrix и полевой pilot остаются открытыми внешними checkbox.

## Текущее доказательство Slice 29

Реализованы локальные readiness API/GUI/metrics, минутный host probe,
автоматический marker завершённой backup, зашифрованный диагностический пакет,
автономная проверка его inventory/checksums и персональный audit скачивания.
Windows start/stop smoke, 41 script tests, backend unit/integration, frontend
component/typecheck и OpenAPI compatibility подтверждают инженерный baseline.
Подробности: [implemented_slice_29.md](implemented_slice_29.md).

Локальный checkpoint: `249 passed, 1 deselected`, backend line coverage
`77.29%`, Ruff, strict mypy по `262` файлам; 66 frontend test files / 186 tests
и coverage `81.82%` statements / `70.77%` branches / `75.08%` functions /
`87.48%` lines; production PWA build; 21 critical tests; migration
`0033 <-> 0034`; signed journal verification; OpenAPI 363 exact mirror и
трёхузловой acceptance. Browser smoke подтвердил RU/EN без смешения языков,
light/dark, desktop/mobile без overflow и обновление старой PWA-вкладки.

Ошибки исполняемого CI-контракта исправлены локально, но remote workflow на
этих изменениях ещё не запускался. Поэтому checkbox **CI release gates зелёные
на конкретном commit** остаётся открытым и не подменяется локальным прогоном.

Operations checkbox остаются открытыми до проверки на конкретном Linux host с
реальным диском, службой времени, ИБП/NUT и сертификатами. Диагностический
checkbox требует независимого просмотра privacy owner; организационные роли,
runbooks на бумаге, support training, RTO/RPO и pilot evidence также не могут
быть закрыты результатом автоматических тестов.