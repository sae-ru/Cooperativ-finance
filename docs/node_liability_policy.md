# Ответственность внешнего узла

Статус: обязательная policy architecture; суммы утверждаются governance/legal.

## Основной принцип

Узел является техническим субъектом доверия, но не человеком и не конечным
носителем вины. За узлом всегда стоят:

- организация-владелец;
- технический хранитель;
- security administrator;
- хозяйственный оператор;
- люди, одобрившие capability/limit;
- независимый аудитор или sponsor в предусмотренном объёме.

Подпись node key доказывает происхождение сообщения от ключа узла. Она не
доказывает физическую истинность payload и не создаёт безусловную коллективную
ответственность всех участников организации.

## Контуры ответственности

| Контур | Пример | Основной ответственный |
|---|---|---|
| Техническая сохранность | ключи, backup, host, update | technical/security roles |
| Протокольная корректность | sequence, signature, package format | node operator и release owner |
| Хозяйственная достоверность | товар, обязательство, delivery | подписавшие business roles |
| Лимит доверия | выданный bilateral/federation exposure | approvers trust contract |
| Инцидентная реакция | несвоевременный revoke/notice | security role/owner |
| Алгоритм клиринга | неверная implementation version | release/clearing control roles |

Одна ошибка может затрагивать несколько контуров; causal assessment разделяет
действия и не назначает «вину узла» одной строкой.

## Node Responsibility Record

Для периода действия хранится:

- node и owner organization;
- named role assignments;
- capability и scope каждого человека;
- trust contract и bilateral limits;
- node bond/reserve/insurance references;
- maximum aggregate exposure;
- software/protocol/policy versions;
- custody оборудования и ключевых контейнеров;
- backup/update/audit obligations;
- incident contacts и SLA;
- signatures owner, sponsor/registrar и ответственных.

Смена человека создаёт новую запись. Старый ответственный остаётся в истории за
свой период.

## Ограничение риска узла

Для каждого peer и capability устанавливаются:

- maximum package value/quantity;
- maximum unsettled obligations;
- maximum rights issued/redeemed externally;
- maximum clearing position;
- maximum offline duration;
- allowed critical resources;
- rate/volume limits;
- required confirmations;
- node bond или dedicated guarantee, если применимо.

Система блокирует новую внешнюю exposure сверх лимита. Уже принятые
обязательства не исчезают при уменьшении trust.

## Обеспечение

Node bond не означает, что «все отвечают всеми паями». Это отдельное заранее
ограниченное обеспечение owner/sponsor или role holders:

- связано с конкретным node/capability/period;
- имеет maximum loss и protected amount;
- не используется повторно скрыто;
- увеличивается только новым согласием;
- исполняется после causal assessment и appeal;
- остаток освобождается после закрытия risks и retention period.

Обычные пайщики узла не несут автоматическую дополнительную ответственность,
если она не установлена их явным договором и применимым правом.

## Типовые инциденты

### Компрометация node key

Немедленно: suspend key, quarantine node, notify peers, определить earliest
compromise, проверить события и physical assets. Ответственность зависит от
соблюдения key policy и скорости реакции, а не от самого факта атаки.

### Ложные хозяйственные события

Node signature устанавливает источник, но assessment проверяет business
signers, custody, evidence, сговор и контроль owner. Technical custodian не
отвечает за качество товара без причинной связи.

### Потеря данных

Проверяются backup/recovery obligations, RPO/RTO, скрытие инцидента и ущерб.
Force majeure отделяется от небрежности. Neighbor receipts могут восстановить
часть истории.

### Неверный клиринг

Цикл freeze, proof воспроизводится известной версией, определяется ошибка input,
policy или implementation. Исправление выполняется compensation cycle.

### Несовместимый/вредоносный update

Проверяются release signature, approval, SBOM, test evidence и соблюдение
rollback. Неутверждённая ручная установка повышает ответственность конкретных
technical roles.

## Coverage waterfall

Предлагаемый порядок после финального решения:

1. возврат/исправление конкретного актива или события;
2. compensation ответственного business participant;
3. связанное role bond виновного человека;
4. dedicated node bond owner organization;
5. ограниченная sponsor guarantee, если она явно выдана;
6. federation mutual reserve по отдельной policy;
7. внешние юридические способы взыскания.

Суммарное покрытие не превышает доказанный ущерб. Слой не применяется дважды.

## Sponsor и registrar

Sponsor не отвечает за все действия узла автоматически. Его ответственность
ограничивается явно подписанной guarantee и качеством собственной проверки,
если доказана причинная связь. Registrar отвечает за соблюдение onboarding
procedure, но не становится поручителем без отдельного договора.

## Процесс решения

1. Incident ограничивает новый риск.
2. Сохраняются оба набора событий, keys, receipts и physical evidence.
3. Строится causal graph по людям, организации, software и peer decisions.
4. Определяется damage и maximum contractual exposure.
5. Conflicts of interest исключают участников панели.
6. Принимается reasoned decision и начинается appeal window.
7. Coverage выполняется отдельными событиями.
8. Trust level, limits и rehabilitation plan пересматриваются.

## Реабилитация узла

Узел выходит из quarantine после key rotation, clean restore, integrity check,
physical reconciliation, corrective update, независимого audit и ограниченного
test package. Сначала возвращается `LIMITED`, затем `ACTIVE` по отдельному
решению.

## Acceptance

- у каждого active node есть owner и действующие named roles;
- можно увидеть ответственность на момент любого package/event;
- node bond ограничен и отделён от паёв обычных участников;
- trust limit блокирует новую exposure транзакционно;
- quarantine/revoke не удаляет старые proofs;
- sponsor liability не возникает без явного предела;
- incident проходит appeal и coverage waterfall;
- замена ответственного или owner не переписывает прошлый период.
