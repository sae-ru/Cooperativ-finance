# Реализованный Slice 14: межузловой клиринг

Дата проверки: 2026-07-22.

Статус: инженерный Slice 14 реализован и проверен на изолированном стенде из
трёх узлов с тремя независимыми PostgreSQL. Между узлами нет общей БД и общего
менеджера транзакций. Экономическая финальность определяется подписанным
commit certificate, а техническое применение может быть идемпотентно завершено
после восстановления связи.

Это доказательство готовности программного контура, а не разрешение проводить
реальные хозяйственные операции. Юридические правила, независимый аудит,
целевое оборудование, обучение людей и пилот остаются внешними критериями
[production readiness](production_readiness.md).

## Реализованные объекты

- версионированная политика `FEDERATED_NETTING / 1.0.0`;
- межузловое обязательство с исходной суммой, остатком, home nodes, единицей,
  классом ликвидности и ссылкой на подписанное исходное событие;
- цикл с координатором, участниками, affected nodes, input/result/certificate
  hashes и явной машиной состояний;
- подписанные snapshots, prepare receipts, proposal, approvals, commit
  certificate, apply receipts и reconciliation proof;
- локальные позиции узла и связь каждого клирингового entry с точной версией
  обязательства;
- append-only события, peer exchanges и идемпотентные command receipts.

Revision `0018_inter_node_clearing` создаёт доменные таблицы, ограничения,
индексы и защитные триггеры. Денежные и количественные значения обрабатываются
через `Decimal`; float в расчёте отсутствует. Populated downgrade заблокирован.

## Протокол

Координатор последовательно:

1. собирает подписанные snapshots всех участников;
2. получает prepare receipts после локальной проверки obligations, versions,
   trust и bilateral exposure;
3. строит детерминированный proposal из canonical input;
4. собирает независимые approvals всех affected nodes;
5. выпускает commit certificate с полным набором подписанных prepare receipts
   и approvals;
6. доставляет certificate участникам и собирает apply receipts;
7. формирует reconciliation proof после применения на всех affected nodes.

Все межузловые документы подписываются Ed25519 поверх canonical JSON. Peer API
возвращает ровно подписанный canonical document, включая представление времени:
response-model сериализация не имеет права менять байты после подписи.

Participant при commit сначала проверяет envelope, certificate, каждый
вложенный prepare receipt и approval, затем импортирует недостающие
доказательства и применяет только собственные entries. Hash-наборы certificate
обязаны покрывать все required nodes; дополнительные локальные доказательства
не меняют уже выпущенный certificate.

## Финальность и восстановление

До certificate prepare может истечь или быть явно освобождён. После валидного
certificate отмена запрещена: состояние `COMMITTED_PENDING_APPLY` означает
не новый выбор, а обязанность довести уже принятое решение.

Повторная доставка certificate, повторный local apply и повторный recovery
идемпотентны. Узел, отсутствовавший во время commit, после восстановления
получает тот же certificate, проверяет его независимо и выдаёт собственный
apply receipt. Coordinator затем получает недостающий receipt и завершает
reconciliation. Ни повторный recovery, ни повторная доставка не изменяют
балансы второй раз.

## Лимиты и персональная ответственность

Prepare блокирует только остаток точных obligations и проверяет bilateral
limits под транзакционной блокировкой PostgreSQL. Certificate невозможен без
approval каждого affected home node. Роли разделены:

- `CLEARING_OPERATOR` собирает evidence и публикует proposal;
- `CLEARING_CONTROLLER` независимо подтверждает локальный результат;
- `CLEARING_FINALIZER` выпускает certificate и выполняет recovery;
- владельцы и именованные ответственные узла отвечают в пределах trust contract,
  node bond и доказанной причинной связи.

Обычные паи участников не списываются автоматически. Любое имущественное
последствие требует отдельной утверждённой policy, bounded reserve, due process
и appeal; клиринг сам по себе только уменьшает встречные обязательства.

## API и GUI

Локальный API предоставляет создание политики, обязательства и цикла, чтение
evidence и отдельные идемпотентные команды collect snapshots, prepare,
proposal, collect approvals, local approval, commit, recovery и release.
Межузловые операции идут через единый authenticated peer endpoint и отдельные
capabilities протокола.

Рабочее место `Межузловой клиринг` показывает:

- активные и ожидающие применения циклы;
- подготовленные и открытые обязательства;
- input, result и certificate hashes;
- readiness каждого узла по snapshot, prepare, approval и apply;
- экономическую финальность и lagging nodes;
- только те команды, которые разрешены текущей роли и состоянием.

## Трёхузловое испытание

Стенд `compose.federation-test.yaml` поднимает `node-a`, `node-b`, `node-c` и
три независимые базы. Fixture создаёт только необходимые identities, roles,
trust contracts, limits и obligations, не используя общие демоданные.

Acceptance-сценарий проверяет:

- шесть направлений доверия между тремя узлами;
- signed snapshots, prepare и детерминированный proposal;
- независимые локальные approvals;
- недоступность `node-c` в момент commit;
- финальное применение на `node-a` и `node-b`;
- восстановление `node-c`, повторную доставку того же certificate и
  идемпотентный повторный recovery;
- точные остатки obligations, позиции и exposure на каждой БД;
- полную reconciliation без общей БД.

Результат контрольного прогона: `1 passed in 11.05s`.

Запуск на Linux/macOS:

```sh
sh ./scripts/test-federation.sh
```

Запуск в PowerShell:

```powershell
.\scripts\test-federation.ps1
```

При ошибке harness печатает состояние и последние логи всех узлов. По
умолчанию topology и volumes удаляются; для расследования можно установить
`KEEP_FEDERATION_TEST_STACK=1`.

## Браузерная приемка

Рабочее место проверено на развернутом Docker-стенде с реальными демоданными:

- desktop и mobile `390x844` не имеют глобального горизонтального переполнения;
- активный пункт длинной мобильной навигации автоматически прокручивается в видимую область;
- горизонтальная прокрутка нижней навигации сохраняется без системного scrollbar;
- обязательный экран смены bootstrap-пароля позволяет штатно завершить сессию;
- циклы, обязательства и политики загружаются через действующий API;
- browser console не содержит `error` и `warn`.
## Проверки

- fresh migration до `0018_inter_node_clearing`: PASS;
- Ruff: PASS;
- strict mypy: 223 source/test files, PASS;
- backend: 158 tests, PASS; 1 multi-node acceptance test выполняется отдельно;
- backend coverage: 75.16%, порог 75%;
- frontend: 47 files / 115 tests, PASS;
- frontend coverage: 82.50% statements, 70.87% branches, 75.83% functions,
  88.67% lines;
- frontend typecheck и production PWA build: PASS;
- OpenAPI: 254 paths;
- backend/frontend OpenAPI SHA-256:
  `B115779E9C87F0119E724FFEEFDFCE2E4296DA9F3AAAEC3743899BE6C0208187`;
- трёхузловой acceptance: `1 passed in 11.05s`.

Обычный backend-набор дополнительно проверяет фильтрацию snapshot по valuation
unit и участникам цикла, coexistence нескольких capability-specific exposure,
readiness schema `0018`, malformed nested artifacts и точность canonical peer
response.

## Незакрытая граница

Реальный запуск запрещён до утверждения valuation units, лимитов, состава
независимых контролёров, порядка ответственности и апелляции. Также необходимы
independent security/legal review, проверка миграции с фактического release,
restore на резервном оборудовании, capacity/accessibility matrix, управление
production-ключами и завершённый пилот. Эти пункты намеренно не закрываются
результатом контейнерных тестов.
