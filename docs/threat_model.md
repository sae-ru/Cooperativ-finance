# Модель угроз

Статус: исходная threat model; обновляется на каждом значимом slice.

## Активы

1. Физические товары и доказательства их существования.
2. Товарные права, обязательства, паи и лимиты.
3. Подписанный журнал и clearing proofs.
4. Закрытые ключи и recovery material.
5. Персональные, медицинские и whistleblower данные.
6. Политики кризиса, санкций и распределения помощи.
7. Работоспособность локального узла и backups.

## Потенциальные нарушители

- внешний атакующий без учётной записи;
- обычный участник, превышающий полномочия;
- оператор или администратор;
- сговор двух обязательных ролей;
- контролёр, аудитор или арбитр с конфликтом интересов;
- захвативший устройство или съёмный носитель;
- скомпрометированный соседний узел;
- поставщик вредоносного обновления;
- лицо, оказывающее физическое давление на подписанта.

## Доверительные границы

Browser/API, API/PostgreSQL, API/blob store, worker/outbox, node/package,
operator/key store, backup/media, system/paper process.

## Ключевые attack paths

| Угроза | Контроль | Остаточный риск |
|---|---|---|
| выпуск сверх остатка | row lock, CHECK, reservation, unique, property test | сговор о ложном физическом остатке |
| двойное погашение | conditional transition, unique redemption, idempotency | компрометация нескольких ролей |
| подмена партии | custody chain, dual attestation, evidence hash | физическая фальсификация доказательств |
| переписывание журнала | append-only DB role, hash chain, external roots/backups | полный захват узла до публикации root |
| replay package | inbox unique, nonce, sequence, expiry | stolen active node key |
| кража ключа | encrypted storage, step-up, rotation, limits | coercion владельца активного ключа |
| admin escalation | separation of duties, signed recovery/break-glass events, source/scope/expiry | сговор двух контрольных сотрудников |
| кража пароля критической роли | Argon2id, TOTP, server-side step-up, revoke | кража пароля и активного устройства одновременно |
| replay TOTP | moving-counter replay guard, ±30 секунд, brute-force lock | захват активной server-side сессии внутри step-up TTL |
| единоличное восстановление себе | запрет requester/target/decider, персональная постоянная роль, signed event | сговор независимого approver |
| превращение break-glass в постоянную роль | отдельный source/lifecycle, обычные role endpoints отклоняют, max 60 минут | ошибочно слишком широкий allowlist/scope |
| подмена подтверждённых условий | versioned canonical hash, подпись каждой стороны, optimistic lock | принуждение стороны подтвердить ложные условия |
| завышение исполнения | submitted reserve, независимая приёмка кредитором, quantity CHECK | сговор должника и кредитора |
| подмена перевозчика | named carrier member/user, role scope, evidence на pickup/delivery | передача физического доступа без фиксации |
| конфликт интересов решающего спор | запрет opener/debtor/creditor, role check, signed resolution | скрытая аффилированность |
| арбитр рассматривает собственное решение | actor separation, обязательная conflict declaration, panel snapshot | скрытая связь вне реестра |
| обход временной меры через соседний API | shared enforcement в identity/risk, typed blocked actions, integration tests | новый модуль не подключил enforcement |
| удаление отмененного решения или факта | append-only trigger, correction event, journal chain | DB superuser может нарушить локальное хранение |
| круговое поручительство | guarantee graph, cycle/depth check, aggregate exposure | скрытые отношения вне известных данных |
| перерасход пая конкурентными acceptance | cooperative advisory lock, повторный preview, account/group limits | захват БД superuser |
| взыскание защищённого пая | отдельный protected amount, coverage cap, assessment без execution | неверно утверждённая policy |
| самоутверждение policy или связанности | active membership, dual control, запрет инициатора/сторон | скрытая аффилированность проверяющего |
| оценка ущерба выше max loss | aggregate DB/domain check, optimistic version, signed assessment | сговор при установке исходного max loss |
| подмена frozen input клиринга | immutable snapshot, obligation versions, input hash | захват DB superuser до внешней фиксации root |
| недетерминированный или подменённый результат | versioned pure engine, canonical hashes, proof verifier, golden/permutation tests | ошибка утверждённого алгоритма |
| двойная финализация цикла | row/advisory locks, optimistic version, idempotency, unique proof | отказ узла внутри недоступной транзакции |
| клиринг спорного обязательства | dispute window, evidence, final recheck, independent resolver | скрытое давление на участника не открыть спор |
| сговор operator/controller/finalizer | раздельные active roles, actor refs, signed journal, audit | реальный сговор трёх лиц |
| покупка репутации | donation excluded from formula | социальное давление вне системы |
| присвоение помощи | dual approval, delivery proof, recipient complaint | давление на получателя/свидетеля |
| ложная нуждаемость | minimal evidence, independent review, appeal | невозможность полной проверки в кризис |
| вредоносное обновление | release signature, SBOM, offline verify, rollback | компрометация release keys/build chain |
| ransomware | least privilege, offline immutable backup, restore drill | одновременная потеря ключей и backups |
| утечка PII | data separation, scopes, encrypted blobs, audit | screenshots и физический доступ |
| DoS локального узла | limits, local network, degraded runbook, paper forms | потеря энергии/оборудования |

## Abuse cases, обязательные для тестов

- один человек создаёт обе обязательные подписи через разные аккаунты;
- отозванная роль подтверждает pending approval;
- инициатор или получатель recovery пытается одобрить собственный запрос;
- один TOTP-код повторно используется для двух step-up;
- серия неверных TOTP не приводит к локальной блокировке;
- пользователь с одним только break-glass пытается восстановить доступ или выдать новое аварийное право;
- временная роль остаётся доступной в уже открытой сессии после revoke/expiry;
- обычный endpoint назначения роли пытается активировать break-glass authority anchor;
- оператор меняет terms после первой подписи;
- повторный Idempotency-Key используется с другим payload;
- сторона подтверждает прежний hash после пересмотра условий;
- должник предъявляет больше остатка или два исполнения одновременно;
- другой логистический пользователь пытается продолжить принятый заказ;
- заявитель, должник или кредитор пытается разрешить собственный спор;
- два workers одновременно применяют одну outbox запись;
- два клиента резервируют последний остаток;
- импорт содержит валидную подпись и несовместимую policy version;
- package пропускает часть node sequence;
- backup manifest валиден, но blob отсутствует;
- администратор пытается удалить signed event;
- донор получает повышенный score или приоритет помощи;
- арбитр рассматривает собственное первоначальное решение;
- сторона дела или арбитр с конфликтом пытается вынести решение;
- исходный арбитр пытается решить апелляцию;
- отмененное решение пытаются обновить или удалить вместо correction;
- активный запрет новой гарантии обходят через bounded-risk API;
- глобальный аудитор не входит в cooperative member list, но UI подменяет выбранный профиль;
- protected share reserve попадает во взыскание;
- два одновременных acceptance резервируют один и тот же остаток;
- владелец подтверждает старый terms hash после изменения commitment;
- одна liability assessment или их сумма превышает max loss;
- инициатор policy, сторона related link или владелец пая принимает собственное контрольное решение;
- operator меняет obligation после freeze и пытается финализировать старый preview;
- controller подтверждает другой input/result hash или собственный preview;
- участник открывает спор без READY evidence либо решает собственный спор;
- два finalizer одновременно применяют один clearing result;
- proof содержит валидный внешний hash, но подменённый input, parameters или result;
- отрицательный quantity_cleared или сумма fulfilled/cleared превышает total;
- PWA показывает локальный draft как подтверждённую операцию.

## Физические угрозы

Software не подтверждает физический факт самостоятельно. Для критического
товара нужны независимые роли, выборочные сверки, меры/весы, custody transfer,
фото или акт, неожиданный аудит и процедура расхождения.

## Риски, которые нельзя устранить кодом

- юридическая неисполнимость договоров;
- недостоверная исходная оценка товара;
- массовый сговор сообщества;
- физическое насилие и захват склада;
- длительная потеря энергии и оборудования;
- дискриминационная политика, утверждённая организацией;
- отсутствие реальных резервов.

Для них требуются governance, аудит, внешние наблюдатели, бумажный контур и
условия остановки пилота.

## Delta Slice 9: солидарная помощь

| Угроза | Реализованный контроль | Остаточный риск |
|---|---|---|
| обещание показано как доступный актив | pledge хранится отдельно и исключён из balance query | оператор может неверно объяснить смысл вне системы |
| двойное распределение последнего остатка | cooperative advisory lock, повторный bucket balance, concurrency test | DB-superuser или физически ложный приход |
| оператор утверждает собственное решение | разные actor ids, controller role, conflict exclusions, signed approval | скрытая связь между людьми |
| фиктивный вклад или выдача | READY content-addressed evidence, независимая проверка, recipient/witness attestation | сговор и постановочное доказательство |
| утечка личности получателя | private condition, минимальные DTO, обезличенный immutable report | screenshot или физический доступ оператора |
| покупка репутации вкладом | отсутствие записей в reputation/risk/obligation и cross-module integration test | социальное давление вне системы |
| потеря жалобы | OPEN complaint приостанавливает невыданный allocation; решение append-only | давление не подавать жалобу |
| destructive rollback скрывает выдачи | downgrade guard по данным/ролям, append-only records, journal hash-chain | захват DB-superuser и резервных копий |

Обязательные abuse tests: два concurrent approval сверх остатка; donor/verifier совпадают; proposer пытается сам утвердить allocation; чужой участник читает заявку; pledge увеличивает balance; donation создаёт debt или reputation benefit; runtime-role обновляет delivery/report.
## Delta Slice 10: резервы и кризис

| Угроза | Реализованный контроль | Остаточный риск |
|---|---|---|
| ложный запас повышает статус резерва | только physical verified snapshot с READY evidence, independent role и append-only history | сговор контролёра и физическая подделка акта |
| бессрочная чрезвычайная власть | review/expiry/maximum end, предел 90 дней, capability check по текущему времени | давление на независимого reviewer |
| operator активирует собственный режим | разные person ids для proposal/activation, scoped roles, signed events | сговор двух людей |
| stale snapshot подтверждает лишнюю выдачу | frozen snapshot hash, age bound, повторная проверка при confirm | ложный свежий physical count |
| два плана резервируют один остаток | cooperative advisory lock и повторный aggregate reserve; concurrency test | DB-superuser |
| protected minimum игнорируется weighted формулой | отдельный базовый слой, shortage делится поровну, unit tests | дискриминационная eligibility policy вне алгоритма |
| скрытая смена нормативов/правила | versioned terms hash и signed atomic retirement прежней policy | governance утвердил вредное правило |
| policy заменяется с открытыми выдачами | rotation блокируется active mandate или PROPOSED/RESERVED allocations | ошибочная ручная сверка после физической выдачи |
| approving person выдаёт ration себе | запрет recipient/confirmer/issuer overlap и evidence | подставной получатель |
| подделка или replay бумажной формы | cooperative serial unique, checksum, expiry, independent record, payload hash | украденный оригинал до регистрации |
| истёкший mandate продолжает действовать | capability enforcement вычисляет effective expiry без ожидания worker | неверные часы узла |
| rollback скрывает режим или выдачи | populated downgrade guard, append-only records, journal chain | захват DB-superuser и backups |

Обязательные abuse tests: concurrent confirm одного verified stock; rejected stock
увеличивает reserve; committed превышает verified; proposer активирует свой
mandate/rule; confirm использует stale snapshot/hash; выдачу выполняет получатель;
paper form вводится повторно или после expiry; rule заменяется с открытым
allocation; истёкший mandate пытается выполнить capability; runtime-role изменяет
snapshot/review/issuance/report.

## Дополнение Slice 11: federation и recovery

| Угроза | Контроль |
|---|---|
| подмена узла | passport key, technical challenge, independent audit |
| захват одним человеком | пять named assignments и несовместимые роли |
| replay пакета | package id, source checkpoint, inbox uniqueness, idempotent apply |
| zip/path bomb | bounded deterministic package parser и manifest allowlist |
| fork/rollback истории | previous checkpoint, signed receipts и conflict state |
| превышение внешнего риска | active contract, bilateral limit, bond и exposure lock |
| скомпрометированный ключ | incident, quarantine, revoke timeline, dual-proof rotation |
| фиктивный бумажный ввод | serial/QR/checksum, signatures, evidence и independent recorder |
| повреждённый backup | SHA-256, archive parse, signed journal и isolated restore drill |
| вредоносный offline release | independently provisioned public key, signature/checksums, OCI digest |

Оставшийся высокий организационный риск: один оператор может владеть host,
backup media и recovery material вне контроля приложения. Production требует
разделения хранителей, FULL restore drill и независимого security review.

## Дополнение Slice 13: онлайн-поиск и резервы

| Угроза | Реализованный контроль | Остаточный риск |
|---|---|---|
| подмена offer/quote или home node | canonical signature, certificate fingerprint, target/source binding, sequence и expiry | компрометация действующего ключа до quarantine |
| replay межузловой команды | message uniqueness, request hash и возврат прежнего signed response | потеря доступности при переполнении до gateway limit |
| SSRF через endpoint узла | endpoint берётся только из audited node record, protocol/capability gate, bounded transport | ошибочно утверждённый внутренний endpoint |
| oversell товара или capacity | row/advisory locks, повторный aggregate active hold, concurrency tests | DB-superuser или ложный исходный остаток |
| browser подсовывает подпись удалённого узла | external signature отсутствует в public reserve DTO; backend сам вызывает home node | захват buyer backend |
| превышение риска peer | exact bilateral limit, package/unsettled checks и locked NodeExposure | governance установил чрезмерный лимит |
| зависшая частичная saga | durable COMMITTING/CANCELLING, idempotent remote ack, retry с исходной version, expiry worker | длительная недоступность peer требует операционного решения |
| освобождение уже committed ресурса | home node запрещает release после commit | компенсация после commit требует отдельного хозяйственного процесса |
| HTTP interception | production-like mode требует HTTPS, доступен CA/client certificate, signature остаётся обязательной | неверная PKI/host-конфигурация |
| утечка каталога | capability/data-scope contract, bounded query и publish precision | разрешённый peer агрегирует легально полученные данные |

Обязательные abuse tests: altered replay; wrong source/target/capability;
expired envelope; неверная response signature; oversell локальным и удалённым
hold; reserve без bilateral limit; превышение package/exposure; клиентская
external signature; partial commit/cancel recovery; expiry и release exposure.

## Дополнение Slice 14: межузловой клиринг

| Угроза | Реализованный контроль | Остаточный риск |
|---|---|---|
| coordinator меняет snapshot или proposal | canonical hashes, подписи участников и независимое воспроизведение result | сговор всех affected controllers |
| commit без согласия affected node | certificate требует полный required-node набор approvals | ошибочно утверждённый состав affected nodes |
| превышение bilateral exposure | prepare под DB lock и точные Decimal limits | governance установил чрезмерный лимит |
| response меняется после подписи | возврат exact canonical signed document и byte-level test | компрометация runtime до подписи |
| lagging node применяет неполный certificate | полный пакет prepare receipts/approvals и независимая проверка каждой подписи | длительная потеря узла после финальности |
| повторная доставка списывает остаток дважды | unique certificate apply, idempotency и transaction locks | захват DB-superuser |
| timeout отменяет уже committed результат | release разрешён только до certificate; после него recovery обязателен | ошибочная ручная процедура вне приложения |
| один человек собирает и финализирует цикл | раздельные operator/controller/finalizer capabilities и actor evidence | скрытый сговор или общие учётные данные |

Обязательные abuse tests: altered canonical response, чужой signer, missing или
extra required hashes, expired prepare, changed obligation version, insufficient
limit, duplicate apply, conflicting certificate и release после finality.
## Дополнение Slice 19: проверка аномалий

| Угроза | Реализованный контроль | Остаточный риск |
|---|---|---|
| алгоритм автоматически обвиняет участника | только `WARN`/`HOLD`, `automatic_decisions=0`, решение человеком с evidence | внешнее давление считать сигнал доказанной виной |
| детектор снимает собственное удержание | разные member ids, DB CHECK, role/scope check и signed actors | скрытая связь между разными людьми |
| оператор меняет причину после обнаружения | immutable facts/thresholds/rule/subject trigger и journal event | DB superuser и захват signing key |
| ложное снятие удержания | только назначенный reviewer, READY evidence, rationale и optimistic version | сговор аудитора с участником |
| обход удержания через другой endpoint | общий enforcement для offer, quote, purchase intent и share exposure | новый хозяйственный endpoint не подключил enforcement |
| повторный запуск заваливает очередь дублями | partial unique active signal и recurrence counter | разные правила описывают один реальный эпизод |
| ложное срабатывание медианного правила | минимум три сопоставимых записи и показ факта/порога | малый или скоординированно искажённый рынок |
| глобальная node role создаёт запись вне cooperative scope | обязательный persisted `cooperative_id`, fail-closed выбор членства и owner-scoped checks каждого связанного объекта | ошибочное администрирование членств |
| signal API раскрывает чужой кооператив | cooperative role scope и scoped predicates | неверно назначенная глобальная роль |
| антифрод превращается в скрытый social score | нет агрегированного персонального балла, reputation event или автоматической санкции | ручное внесистемное ранжирование |

Обязательные abuse tests: изменение immutable основания прямым SQL; детектор с
ролью аудитора начинает собственное рассмотрение; пользователь повторяет
заблокированную команду через соседний API; решение без READY evidence; stale
`expected_version`; повторный scan одного active signal; роль из другого
кооператива читает очередь.

## Дополнение Slice 21: связанные участники и межконтурный сговор

| Угроза | Реализованный контроль | Остаточный риск |
|---|---|---|
| взаимная накрутка связанных аккаунтов | active related link и положительные события в обоих направлениях | незафиксированная связь или посредники вне компоненты |
| дробление ниже лимита | достижимый near-limit WARN по серии однотипных малых обязательств | законная сезонная серия даёт false positive |
| концентрация критического ресурса | active reserve target, verified/frozen positive stock и транзитивная related component | ложный физический остаток или скрытый бенефициар |
| синхронные оценки | один context, 10-minute window, минимум два автора | медленная координация вне окна |
| доверие покупается помощью | verifier взноса совпадает с автором последующей положительной оценки | вознаграждение через третье лицо или позднее 72 часов |
| оператор решает в пользу связанного лица | direct related link для allocation/approval/arbitration и `HOLD` | незадекларированная связь |
| кампании дробятся ради обхода контроля | три пересекающиеся кампании одного creator/fund | координация нескольких формально разных creators |
| санкция обходится новым аккаунтом | active sanction, active related link и member создан после начала санкции | новая личность не связана в identity index |
| каталог создаёт ложное чувство точности | version/hash/dataset в scan, synthetic scope и `production_approved=false` | оператор игнорирует предупреждение и использует rule как обвинение |

Правила теперь покрывают все классы раздела 24.5, но организованный сговор нельзя
считать полностью выявленным. Нужны пилотный размеченный набор, измерение ложных
срабатываний, drift/adversarial/privacy review и обязательное декларирование
связей. Необъяснимая итоговая «карма риска» и автоматическая санкция по сигналу
по-прежнему запрещены.

## Дополнение Slice 24: внешние программные интеграции

| Угроза | Реализованный контроль | Остаточный риск |
|---|---|---|
| внешнюю программу заводят как человека с широкими ролями | отдельные `ServiceClient`, credential и token tables; machine token не проходит human dependencies | администратор вручную создаст лишний human account вне процедуры |
| один человек незаметно подключает свою программу | permanent manager request, отдельный permanent security reviewer, personal member и TOTP step-up | сговор двух сотрудников или общая TOTP-учётная запись |
| break-glass превращается в постоянный machine access | `RoleGrantSource.ASSIGNMENT` обязателен для request/decision/protective actor | неверно выданная постоянная роль |
| secret утекает из БД, журнала или idempotency replay | хранится только hash/prefix, secret возвращается один раз и отсутствует в replay/audit/journal | screenshot, clipboard, лог внешней программы или плохое secret storage |
| похищенный token используется из другой сети | token server-side revocable, source-IP bound, credential/client/owner recheck на каждом запросе | атакующий находится в разрешённой сети |
| подделка `X-Forwarded-For` обходит allowlist | API доступен только через штатный gateway; forwarded IP считается доверенным только от изолированного proxy | ошибочная публикация API-порта или дополнительный недоверенный proxy |
| allowlist случайно открывает весь Интернет | CIDR normalization и запрет IPv4/IPv6 `/0`; GUI требует явно заполнить пустой allowlist | слишком широкий, но не `/0`, утверждённый диапазон |
| интеграция получает скрытые новые возможности | точный allowlist из двух scopes и `require_scope` на каждом runtime endpoint | будущий endpoint забудет scope enforcement |
| каталог используется как SSRF/direct peer scanner | service API запрещает `DIRECT`, сохраняет bounded search и действующие federation policies | разрешённая indexed выдача агрегируется внешней программой |
| бухгалтерская выгрузка чужого кооператива | token owner cooperative сравнивается с clearing cycle owner | чрезмерная глобальная human role читает admin metadata |
| credential rotation оставляет старые sessions | active credential retires, все tokens client отзываются в одной transaction | внешняя программа продолжает хранить старый secret и создаёт шум входа |
| защитный отзыв ломает работу людей | service transitions не изменяют `User`, role или human session | интеграция была единственным практическим способом выполнить процесс |
| rate-limit таблицы или token history растут без границ | PostgreSQL minute buckets, worker retention 2/30 days и cleanup integration test | worker outage временно увеличивает объём таблиц |
| PII технического контакта утекает в signed federation journal | contact остаётся в owner-scoped identity table и исключён из signed payload | authorised auditor или backup custodian видит contact data |

Обязательные deployment checks: API container не публикует host port; только
gateway находится в edge network; proxy очищает входные forwarded headers;
allowlist соответствует реальному egress внешней программы; secret сохраняется в
отдельном secret store; compromised-secret drill доказывает rotation/revoke и
непрерывность human login. Эти проверки требуют независимого security review и
не закрываются component/integration tests.
## Дополнение Slice 25: объединение дубликатов участников

| Угроза | Реализованный контроль | Остаточный риск |
|---|---|---|
| оператор удаляет неудобную карточку и её историю | source не удаляется, status `MERGED` требует survivor self-FK, mapping входит в signed event | ошибочно выбран правильный survivor |
| один сотрудник объединяет себя или связанное лицо | permanent registrar/data steward request, другой permanent security reviewer, personal actor и TOTP | сговор двух сотрудников или общая TOTP-учётная запись |
| break-glass используется для сокрытия identity | request/review принимают только `RoleGrantSource.ASSIGNMENT` | неправомерно выданная постоянная роль |
| merge переписывает автора старого события | journal и все другие non-identity FK являются blocker; исторические refs не обновляются | юридический оператор вручную создаст компенсирующие события неверно |
| новый модуль забыли добавить в blocker registry | PostgreSQL function динамически перечисляет фактические FK на `identity.members` | ссылка без FK или внешний blob/search index не обнаружены |
| две учётные записи дают захват доступа | обе user links дают `IDENTITY_ACCOUNT_CONFLICT`; переносится только единственный source user при пустом survivor | администратор заранее ошибочно отключит нужный login |
| unique collision ломает merge частично | membership/address/account conflicts проверяются до update и повторно под row locks; одна transaction | будущая identity unique constraint не добавлена в preflight, но transaction rollback сохранит данные |
| карточка изменилась после создания дела | versions source/survivor записаны в case и повторно проверяются при решении | изменение во внешнем хранилище без version/FK |
| старое pending дело навсегда блокирует source | expiry показывается в read model, при новой заявке старое дело закрывается signed expiration event | без новой заявки физический status остаётся pending до review/cleanup |
| blocker summary раскрывает PII или схему БД | signed/API payload содержит только codes/counts; GUI группирует schema по предметным областям и не показывает table/column | администратор с прямым DB-доступом видит metadata |
| cross-cooperative merge обходит юридический контур | обе карточки обязаны иметь тот же `registered_by_cooperative_id`; иначе fail-closed | ошибочная исходная cooperative attribution требует отдельной correction procedure |
| цепочка merge скрывает происхождение | survivor со status `MERGED` запрещён; source с inbound merge self-FK обнаруживается как blocker | будущая утверждённая consolidation цепочка потребует отдельной модели aliases |

Перед production независимый review должен проверить полный FK coverage, таблицы без FK, backup/restore merge history, operator selection UX и процедуру компенсации ошибочного решения. Автоматический перенос паёв, долгов, поручительств, sanctions и reputation запрещён до отдельных доменных workflows.

## Дополнение Slice 26: выход и преемственность

| Угроза | Контроль | Остаточный риск |
|---|---|---|
| Один администратор выводит участника из системы | постоянный персональный requester, независимый `SECURITY_ADMIN`, TOTP и self-review block | сговор двух операторов требует внешнего надзора |
| Участник действует после сообщения | логины отключаются, все sessions отзываются, membership приостанавливается атомарно | внешние незавершённые операции требуют reconciliation |
| Ошибочное сообщение уничтожает доступ | точный versioned snapshot и reject restore без старых sessions | пользователь должен безопасно войти заново |
| Гонка возвращает устаревшее состояние | row locks, version/status blockers, lifecycle `BLOCKED` | ручная проверка может быть длительной |
| Универсальный перенос присваивает паи/долги | экономические FK только группируются и никогда не перенаправляются общей командой | legal succession procedures ещё не реализованы |
| Evidence раскрывает персональные данные | bounded reference/hash, запрет document body в событии и GUI warning | оператор может нарушить регламент ввода |
| Downgrade теряет защитное состояние | migration fail-closed при cases/new statuses | аварийный rollback требует ручного утверждённого плана |

## Дополнение Slice 27: аварийная физическая сохранность

Угрозы: самоназначение администратора, фиктивная инвентаризация, подмена партии между пересчетом и приемкой, автоматическая корректировка недостачи, приемка чужой учетной записью и использование break-glass как постоянной роли. Контроли: разделение четырех персональных субъектов, постоянные роли, TOTP, per-lot hold, optimistic snapshots, content-addressed evidence, повторная блокировка строк перед решением, атомарная смена назначений и подписанный журнал. Остаточный юридический риск собственности и наследования не маскируется технической передачей хранения.

## Дополнение Slice 28: production deployment

| Угроза | Контроль | Остаточный риск |
|---|---|---|
| wrapper называется production, но приложение работает как dev | canonical resolver и persisted `.env`; alias отклоняется | оператор запускает контейнеры вне поставляемых scripts |
| demo data/пароли попадают в реальную эксплуатацию | config, known credentials и DB marker блокируют promotion | прямое злонамеренное изменение БД/volumes вне модели доверия |
| локально изменённый source собирается при установке | обязательный signed bundle и `--no-build --pull never` | verifier/public key скомпрометированы вместе |
| подписан bundle с неутверждённой license policy | обязательный independently pinned policy SHA-256 | неверный hash утверждён организационно |
| параметры проверенной поставки теряются после закрытия shell | bundle/key paths и policy hash сохраняются в `.env`; backup/update читают их без исполнения файла | путь bundle стал недоступен или storage повреждён |
| production update включает faultpoint или DATA_ONLY backup | canonical production guard в обеих ОС | оператор обходит scripts и вручную меняет Compose state |
| evidence скрывает dirty source | clean worktree обязателен, override запрещён | committed malicious change требует review/CI/signature controls |
| режим меняют только переменной Compose | PostgreSQL profile блокирует demo marker и hardened transition | физический DB administrator может подменить state; это должно попасть в external audit |
