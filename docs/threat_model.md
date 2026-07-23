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
| admin escalation | separation of duties, audit, break-glass alert | сговор security admins |
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
