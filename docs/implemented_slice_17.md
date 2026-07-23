# Реализованный Slice 17: критические quality gates

Статус: инженерные property, concurrency, migration и OpenAPI gates реализованы
и проверены на изолированном Linux/Docker-совместимом стенде.

## Цель

Релиз не должен считаться пригодным только потому, что один раз прошел общий
набор тестов. Для экономически критичных операций нужны воспроизводимые
контракты, проверка миграции с предыдущей схемы и несколько последовательных
прогонов конкурентных сценариев в общей PostgreSQL-базе.

## OpenAPI compatibility

scripts/openapi_compat.py сравнивает принятый baseline с текущим контрактом и
завершается ошибкой при неопределенном или несовместимом результате.

Gate проверяет:

- удаление path, operation, parameter, request body, response и media type;
- изменение operationId;
- появление нового обязательного parameter/body/property;
- сужение допустимых request types, enum и числовых границ;
- ослабление гарантированных response types, enum, required и границ;
- изменение composition schema как требующее review;
- разрешимость только локальных $ref;
- точное побайтовое совпадение backend и frontend snapshot.

Первый baseline хранится в infra/contracts/openapi-0.1.0.json. На проверенном
состоянии baseline, backend/openapi.json и frontend/openapi.json имеют SHA-256
b115779e9c87f0119e724ffeefdfce2e4296da9f3aaaec3743899be6c0208187
и содержат 298 операций. Семь негативных тестов проверяют fail-closed
поведение, включая изменение компонента за внешне неизменным $ref.

Backend test image содержит committed snapshot, а API test сравнивает его с
фактически сгенерированным приложением документом. Поэтому одновременно
контролируются приложение, backend snapshot, frontend mirror и release
baseline.

## Property matrix

Помимо постоянных golden vectors выполняются детерминированные матрицы:

- 200 сгенерированных локальных clearing graphs;
- 100 сгенерированных federated clearing graphs;
- исходный, обратный и перемешанный порядок для каждого graph;
- точное совпадение result hash;
- amount_before = cleared + amount_after;
- 0 <= cleared <= amount_before;
- сохранение общей суммы локальных движений;
- сохранение net position каждого узла.

Seed фиксирован номером примера, поэтому любое падение воспроизводится.

## Migration gate

scripts/test-migration.sh создает отдельный Compose project и выполняет:

1. чистую установку миграций до 0017_peer_reservations;
2. создание node profile и bootstrap identity текущим приложением;
3. upgrade до 0018_inter_node_clearing;
4. проверку сохранности идентичности и установки critical clearing tables;
5. повторный init/bootstrap как idempotency check;
6. допустимый downgrade до 0017 при отсутствии новых хозяйственных данных;
7. повторный upgrade до 0018 и сверку исходного node profile.

Проверенный прогон:

- таблицы до/после: 118 -> 130;
- critical head tables: 4;
- identity counts: 1:3:0:0:9;
- upgrade, downgrade и re-upgrade завершились на ожидаемых revision.

Это доказывает текущий переход между схемами release candidate. Checkbox
миграции с предыдущего фактического production release остается открытым до
появления такого релиза и его подписанного backup.

## Repeated concurrency gate

scripts/test-critical-quality.sh 3 объединяет OpenAPI report, migration report,
property matrix и повторяемые PostgreSQL concurrency scenarios. Каждый раунд
выполняет 21 тест по следующим контурам:

- выпуск и однократное погашение товарных прав;
- однократная финализация локального клиринга;
- одновременное резервирование кризисного остатка;
- одновременное расходование подтвержденного solidarity balance;
- конкурентное принятие bounded exposure и освобождение commitment;
- сохранение строгой sequence подписанного журнала.

Первый объединенный прогон обнаружил зависимость crisis test от порядка данных:
тест выбирал произвольный evidence без ограничений cooperative/status.
Селектор исправлен на детерминированный READY evidence своего cooperative.
После исправления три полных раунда дали 21 + 21 + 21 passed, а итоговая
проверка журнала подтвердила 556 событий, sequence 556 и отсутствие failures.

Локальный evidence pack: evidence/quality-20260722T104138Z. Он содержит
manifest, OpenAPI и migration reports, вывод каждого раунда, journal
verification и SHA256SUMS. Каталог намеренно исключен из Git как локальное
эксплуатационное доказательство.

## CI и release

Workflow содержит отдельный critical-quality job. Supply-chain release job
зависит от backend, frontend и critical-quality, поэтому подписанный пакет не
создается при провале этого gate.

Offline node payload теперь содержит:

- принятый OpenAPI baseline;
- backend и frontend contracts;
- автономный compatibility checker.

Production evidence collector запускает OpenAPI gate и включает отчет в общий
checksum inventory.

## Команды

Полный gate:

    bash ./scripts/test-critical-quality.sh 3

Только migration:

    bash ./scripts/test-migration.sh

Только OpenAPI:

    python ./scripts/openapi_compat.py --baseline ./infra/contracts/openapi-0.1.0.json --current ./backend/openapi.json --mirror ./frontend/openapi.json

## Остаточные ограничения

Slice не заменяет:

- зеленый удаленный CI на конкретном commit;
- migration с подписанного предыдущего production release и его реальными
  объемами;
- независимый security review;
- capacity и restore measurement на утвержденном минимальном host;
- ручную browser/device/screen-reader matrix;
- юридические решения, custodian ceremony и фактический pilot.
