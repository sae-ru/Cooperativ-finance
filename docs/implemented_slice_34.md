# Реализованный Slice 34: подписанная цепочка ответственности

Статус: реализован code-level baseline для экономически критических команд.
Общий production gate остаётся открытым. Recovery, break-glass и emergency
custody позднее включены в тот же контракт в
[Slice 35](implemented_slice_35.md); role administration, sanctions/crisis и
node authority ещё не закрыты.

## Что защищено

`SignedJournalService` содержит канонический `CRITICAL_EVENT_TYPES`. Для каждого
из 24 зарегистрированных типов отсутствие `CommandAssurance` останавливает
транзакцию до изменения хозяйственного состояния.

Контуры реестра:

- резерв партии, выпуск, передача и погашение товарного права;
- исполнение, операторская reconciliation происхождения, приёмка и клиринг
  обязательства;
- финализация и бухгалтерская reconciliation локального клирингового цикла;
- взнос, резерв и освобождение личной паевой экспозиции;
- authorization, settlement и void компенсации;
- утверждение и выдача солидарной помощи;
- резерв внешнего узла;
- prepare, commit certificate, local apply и reconciliation межузлового
  клиринга.

AST-gate `test_command_assurance_registry.py` разбирает все Python call sites и
проверяет, что каждый зарегистрированный тип действительно передаёт
`assurance=`. Новый тип нельзя молча добавить только в реестр или только в
application service.

## Подписываемый формат v2

В payload события добавляется `_command_assurance`:

- `performed_by`: физический участник и его user account;
- `on_behalf_of`: участник, кооператив или узел, от имени которого действует
  человек;
- `role`: assignment, role code и источник полномочия;
- `scope`: организация команды и scope роли;
- `evidence`: число ссылок и SHA-256 канонического списка;
- `exposure`: категория, эффект, subject, точная величина, единица,
  `maximum_loss` и basis refs;
- `attesters`: заявители физического факта;
- `approvers`: стороны, принявшие требуемое решение;
- `next_responsible`: одна или несколько сторон, которым переходит следующий
  шаг; пустой список означает терминальную хозяйственную ответственность.

Локальные role claims повторно проверяются в БД в момент записи: role и user
должны быть активны, member должен совпадать, а cooperative scope не может быть
подменён. `on_behalf_of` обязан совпадать с actor scope или локальным node.
Пустой evidence, недопустимая exposure, зарезервированный payload key,
двусмысленное одновременное `evidence` и `assurance`, подмена стороны или
неактивная роль приводят к rollback.

## Реальная передача ответственности

Application services указывают не абстрактного «оператора потом», а конкретную
сторону:

- новое товарное право и передача переходят владельцу;
- поданное исполнение переходит получателю на приёмку;
- отклонённый остаток возвращается должнику;
- компенсация после authorization переходит получателю;
- паевой резерв остаётся за владельцем exposure;
- помощь после выдачи переходит получателю;
- commit certificate перечисляет все affected node codes как approvers и
  следующих ответственных.

Independent approvals и attesters подписываются там, где они уже являются
доменным инвариантом: clearing controllers, compensation decision and
authorizer, solidarity proposer and controller, fulfillment recipient,
share-exposure proposer and owner.

## Совместимость истории

Формат `critical-command-assurance-v1`, если он уже попал в журнал, не
переписывается: изменение сломало бы подпись и hash chain. Новые события
используют `critical-command-assurance-v2`. Миграция `0037_actor_assurance`
сохраняет cooperative scope создателя federated cycle; отдельная SQL-миграция
для JSON payload не нужна.

Старые события без v2 являются legacy evidence и требуют операторского
review, а не backfill через UPDATE.

## UX ошибок

Коды `CRITICAL_COMMAND_*`, `COMMAND_ASSURANCE_*` и `ACTOR_*` не показываются
пользователю. RU/EN интерфейс сообщает, что операция не записана, данные не
изменены и следует обратиться к оператору кооператива.

## Проверки

- fail-closed integration: missing assurance, missing evidence, forged scope и
  conflicting evidence;
- точный v2 snapshot performed-by/on-behalf/role/scope/parties/exposure;
- независимая проверка journal signature и hash chain;
- AST coverage всего critical registry;
- rights, fulfillment, clearing, risk, compensation, solidarity и federation
  PostgreSQL flows;
- Ruff, strict mypy, frontend locale/error tests и production build.

Контрольный прогон 28 июля 2026 года:

- backend на чистой PostgreSQL-схеме: `255 passed, 1 deselected`;
- Ruff: без замечаний; strict mypy: `220 source files`;
- frontend: `69` файлов, `197 passed`, typecheck и production build;
- изолированный three-node Docker federation acceptance: `1 passed`;
- миграции с нуля: `0001` -> `0037`.

## Что ещё не закрыто

Этот срез не объявляет любую signed command экономически критической. Следующий
этап должен распространить тот же typed exposure на:

- обычную выдачу и отзыв полномочий;
- sanctions, appeals и окончательные liability decisions;
- crisis mandate, rationing и бумажные операции;
- node trust, limits, bonds, quarantine, key lifecycle и offline authority.

До этого пункт «каждая critical command» в
[production_readiness.md](production_readiness.md) остаётся открытым.
