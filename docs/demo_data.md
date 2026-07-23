# Демонстрационные данные

Демонстрационные данные предназначены только для локальной разработки, проверки интерфейса и обучения оператора. Они никогда не должны автоматически включаться в `staging-node`, `pilot` или `production`.

## Включение

Профиль `demo` запускает идемпотентную команду `coopctl seed-demo`. Команда требует `COOP_DEMO_DATA_ENABLED=true`; конфигурация приложения запрещает это значение в защищённых контурах.

Стандартный файл `.env.example` включает профиль для локального первого запуска. Для узла без демоданных нужно установить:

```dotenv
COOP_DEMO_DATA_ENABLED=false
COMPOSE_PROFILES=
```

## Состав набора Slice 0

- профиль локального узла с признаком `demo_data_loaded=true`;
- предупреждение о необходимости провести тест восстановления из резервной копии;
- информационное сообщение о плановой проверке политик пилота.

Идентификаторы вычисляются детерминированно, а вставки используют upsert. Повторный запуск не создаёт дубликаты и восстанавливает ожидаемое состояние демонабора.

## Очистка

На Slice 0 демоданные размещаются только в отдельной локальной БД. Полное удаление выполняется остановкой стенда и удалением его именованных томов. В будущих срезах очистка боевых хозяйственных событий командой демоданных запрещена.


## Состав набора Slice 1

- локальный кооператив, совпадающий с профилем узла;
- четыре участника в состояниях `ACTIVE`, `PENDING_VERIFICATION`, `LIMITED` и `SUSPENDED`;
- детерминированные членства с номерами `D-0001` - `D-0004`;
- три bootstrap-оператора с разделенными полномочиями. Их пароли не являются демоданными и генерируются отдельно в файловых секретах.
## Состав набора Slice 2

- активная роль `DATA_STEWARD` для назначаемого demo-оператора;
- ответственность за складскую зону с ограниченным объёмом `250.0000 DEMO_UNIT`;
- подписанная цепочка `proposal -> independent approval -> personal acceptance`;
- outbox messages и consumer receipts, созданные теми же production-командами.

Повторный seed возвращает ранее завершённые idempotency records и не добавляет новые signed events. Integration-тесты используют отдельный `postgres-test`, поэтому тестовые события не смешиваются с демонабором узла.

## Состав набора Slice 3

- единица `KG`, товар `CABBAGE` и два склада;
- активные роли `WAREHOUSE_CUSTODIAN` для двух операторов и
  `INVENTORY_CONTROLLER` для независимого контролёра;
- две подписанные цепочки складской ответственности с независимым одобрением
  и личным принятием;
- партия `CABBAGE-DEMO-001`, прошедшая контроль и двухфазную передачу на
  резервный склад;
- партия `CABBAGE-DEMO-002` в статусе `DISPUTED` после количественного
  расхождения;
- партия `CABBAGE-DEMO-003` в очереди независимого контроля;
- четыре READY evidence blob, зашифрованные в именованном `blob-data` volume.

Seed использует те же catalog, evidence, responsibility, inventory и custody
services, что и API. Повторный запуск сохраняет 3 партии, 4 доказательства и
2 активные складские ответственности.
## Состав набора Slice 4

- активная роль `RIGHTS_OPERATOR` у регистратора;
- право `25.00 KG`, переданное от Елены Анне и ожидающее выдачи;
- право `10.00 KG`, защитно замороженное аудитором;
- право `5.00 KG`, фактически выданное хранителем и погашенное;
- два дополнительных READY evidence: согласие на передачу и акт выдачи;
- balance подтверждённой партии с отдельными available, rights issued и
  redeemed контурами.

Seed использует production-команды выпуска, передачи, запроса, заморозки и
погашения. Повторный запуск сохраняет три детерминированных права и не
добавляет повторные signed events.

## Состав набора Slice 5

- сделка `Demo cabbage delivery` между двумя активными участниками на
  `20.00 KG` капусты;
- личные подтверждения обеих сторон одной версии и одного SHA-256 условий;
- одно обязательство с разрешённым частичным исполнением;
- заказ перевозки `8.00 KG`, лично принятый и доведённый назначенным
  `LOGISTICS_OPERATOR` до `DELIVERED`;
- READY evidence погрузки, доставки, предъявления исполнения и приёмки;
- предъявлено `8.00 KG`, кредитор принял `6.00 KG`, а `2.00 KG` освободились
  для замены.

Seed вызывает те же production-команды, что и API. Повторный запуск сохраняет
одну сделку, одно обязательство, один заказ доставки и прежнюю длину signed
journal. Споры не создаются демоданными, чтобы рабочее место по умолчанию
показывало нормальный незавершённый процесс; их lifecycle покрыт тестами.

## Состав набора Slice 6

- active policy `DEMO_SHARE` с индивидуальным лимитом `60`, групповым `100` и
  глубиной поручительств `3`;
- independent approval точного policy hash;
- гарантийный паевой счёт Анны: balance `100`, protected amount `40`;
- personally accepted `DIRECT_OBLIGATION`: reserve `30`, max loss `25`,
  coverage ratio `0.833333`;
- READY evidence предложения/утверждения policy и открытия паевого счёта;
- отсутствие liability case и автоматического исполнения.

Seed использует production-команды risk service. Повторный запуск сохраняет
одну policy, один account, один commitment и прежнюю длину signed journal.
## Состав набора Slice 7

- active policy `LOCAL_NETTING/1.0.0` для unit `DEMO_SHARE`;
- роли `CLEARING_OPERATOR`, `CLEARING_CONTROLLER`, `CLEARING_FINALIZER` у трёх
  разных активных участников;
- встречные подтверждённые obligations Анны и Павла с `clearing_allowed=true`;
- цикл `DEMO-WEEK-2035-01`, прошедший collect, freeze, preview, independent
  approval, ready, finalize и reconcile;
- два clearing entries, один proof, две participant statements и один
  accounting export draft;
- отдельные `quantity_cleared` и `quantity_fulfilled`: клиринг не имитирует
  физическую поставку.

Seed использует production lifecycle и повторно возвращает те же idempotency
results без новых циклов, proof или signed events. Параметры policy являются
только демонастройкой и не разрешены для реального расчёта до закрытия
`OD-003`, `OD-004` и `OD-043`.
## Состав набора Slice 8

- active `TRUST_PROCEDURE/1.0.0-DEMO` с независимым утверждением;
- роли `ARBITRATOR` у двух разных участников и глобальные права аудитора;
- дело `DEMO-TRUST-APPEAL-001` об ошибочной интерпретации часового пояса;
- оригинальное решение `SUBSTANTIATED`, временная мера и warning sanction;
- спорный `BREACH`, независимая апелляция `OVERTURNED` и причинная `CORRECTION`;
- отозванные последствия и отмененный rehabilitation plan без удаления истории.

Seed использует production services и READY evidence. Повторный запуск сохраняет
одно дело, одну апелляцию, два reputation events и прежнюю длину signed journal.
Параметры демополитики не разрешены для реального применения до закрытия
`OD-025`, `OD-029`, `OD-030`, `OD-031` и `OD-032`.

## Состав набора Slice 9

- активные роли `SOLIDARITY_OPERATOR` и `SOLIDARITY_CONTROLLER` у разных участников;
- фонд `DEMO_SOLIDARITY` и кампания `DEMO-AID-001`;
- обещание Анны на `10 KG` капусты, которое отдельно показано как не включённое в баланс;
- физическое поступление с READY evidence и независимой проверкой;
- приватная заявка Нины, eligible review и allocation на `10 KG`;
- жалоба, временное состояние `SUSPENDED` и независимое восстановление;
- delivery, подтверждённая получателем без встречного обязательства;
- закрытая кампания и обезличенный immutable report с остатком `0 KG`.

Параметры являются только демонстрационными и не закрывают `OD-026`, `OD-027`, `OD-028` или `OD-037`. Повторный seed находит прежний report и не добавляет новые записи или signed events.
## Состав набора Slice 10

- роли `CRISIS_OPERATOR` и `CRISIS_CONTROLLER` у разных active участников;
- active target `CABBAGE`: цель `100 KG`, critical minimum `20 KG`;
- physical snapshot с READY evidence: verified/available `50 KG`, rate `10 KG/day`, coverage `5 days`, level `WARNING`;
- ограниченный мандат `DEMO-CRISIS-001` для simulated payment failure;
- equal rationing rule: protected minimum `2 KG`, maximum `5 KG`;
- подтверждённая и выданная Нине allocation `5 KG` без debt/share/reputation effect;
- нумерованная форма `DEMO-PAPER-001`, независимо введённая по checksum;
- independent review, закрытый мандат и immutable crisis report.

Пороговые значения предназначены только для учения. Повторный seed находит
прежний report и не добавляет records, evidence или signed events.

## Состав набора Slice 11

- внешний узел с проверенным passport/public key и пятью раздельными
  responsibility assignments;
- пройденный technical challenge и независимый onboarding audit;
- active trust contract с разрешёнными federation и paper event types;
- bilateral limits, node bond и ограниченный exposure;
- offline epoch, детерминированный signed package, simulation, conflict decision,
  apply и receipt;
- federation paper form `DEMO-FED-PAPER-001`, выданная хозяйственным оператором
  и независимо введённая аудитором с participant signatures и READY evidence;
- роли `NODE_TECHNICAL_CUSTODIAN`, `NODE_SECURITY_ADMIN`,
  `NODE_BUSINESS_OPERATOR` и `NODE_AUDITOR` назначены разным людям.

Повторный seed находит существующий federation node и не создаёт второй договор,
пакет, бумажный оригинал или signed events.

## Состав набора Slice 13

- локальные и импортированные предложения капусты, гвоздей и молока;
- точные quantity/unit/minimum batch, quality и price components;
- локальные logistics quotes с route legs, custody и liability;
- подписанный peer offer index;
- результаты с воспроизводимой `LANDED_COST_V1` и freshness;
- идемпотентный повтор без новых offers, quotes, indexes или journal events.

Demo peer endpoint не используется для реального сетевого резерва. Для него
требуются отдельный доступный узел, `CC-PEER-1`, active certificate/trust
contract и bilateral limits.
