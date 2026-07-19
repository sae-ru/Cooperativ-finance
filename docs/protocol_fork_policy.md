# Обновление и разделение протокола

Статус: production governance protocol.

## Типы изменения

1. Patch: исправление реализации без изменения смысла событий.
2. Compatible extension: новые optional поля/capabilities.
3. Policy change: новая версия экономического правила.
4. Breaking protocol: несовместимый envelope/schema/semantics.
5. Emergency security change: ограниченное срочное отключение capability/key.

## Требования к предложению

- автор и владельцы;
- причина и threat/impact analysis;
- затронутые event/API/data schemas;
- влияние на паи, ответственность, репутацию, помощь и кризис;
- backward/forward compatibility matrix;
- migration и rollback;
- offline nodes и maximum stale period;
- test vectors;
- начало/конец transition window;
- quorum/mandate по утверждённой governance policy.

## Подписанный release/update

Update package содержит old/new version, supported ranges, code/image hashes,
SBOM, migrations, policy documents, test vectors и rollback. Узел проверяет всё
до остановки текущей версии.

## Compatibility negotiation

Node passport объявляет protocol range, capabilities и policy ids. Package
применяется только при совместимом mandatory set. Optional event сохраняется
без применения лишь если это явно безопасно.

## Fork

Если узлы не принимают одну policy, divergence оформляется формально:

- fork id и common checkpoint;
- списки узлов/организаций;
- policies каждой ветви;
- правила старых обязательств, прав, гарантий и disputes;
- прекращение взаимного доверия новых событий;
- export/settlement plan;
- публичный audit notice.

Fork не переписывает события до common checkpoint. Активы и обязательства не
дублируются в обеих ветвях без явной settlement procedure.

## Emergency

Emergency update может временно запретить уязвимую capability или key, но не
изменяет баланс, санкцию, репутацию или очередь покрытия. Постоянная мера
проходит обычное утверждение после incident containment.

## Тесты

- old node читает compatible new package;
- new node проверяет old signatures/proofs;
- breaking package отклоняется до изменения состояния;
- interrupted migration восстанавливается;
- rollback сохраняет принятые хозяйственные события;
- two-node fork не приводит к silent merge после восстановления связи.
