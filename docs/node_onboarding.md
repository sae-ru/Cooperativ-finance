# Подключение внешнего узла

Статус: обязательный trust onboarding protocol.

## Принцип

Подключение узла не является добавлением URL и публичного ключа. Это выдача
ограниченного доверия конкретной организации, оборудованию, программной версии,
ключам и именованным людям на определённый срок и объём операций.

## Стороны

- `NodeOwnerOrganization`: юридически/хозяйственно владеет узлом;
- `NodeSponsor`: существующий доверенный cooperative/node;
- `FederationRegistrar`: принимает onboarding decision;
- `NodeTechnicalCustodian`: оборудование, backup, updates;
- `NodeSecurityAdministrator`: keys, certificates, incidents;
- `NodeBusinessOperator`: разрешённые хозяйственные пакеты;
- `NodeAuditor`: независимая проверка;
- `PeerNode`: принимает packages в пределах bilateral trust.

У каждой роли есть действующий `RoleAssignment`, scope, срок и при
необходимости role bond.

## Состояния

```text
DRAFT
 -> APPLICATION_SUBMITTED
 -> IDENTITY_VERIFIED
 -> TECHNICAL_CHALLENGE
 -> AUDIT_PENDING
 -> LIMITED
 -> ACTIVE
```

Дополнительные: `REJECTED`, `SUSPENDED`, `QUARANTINED`, `REVOKED`, `ARCHIVED`.

Новый узел начинает с `LIMITED`; повышение доверия требует истории успешных
packages и audit.

## Заявка

Содержит:

- node id и owner organization;
- jurisdiction/territory и purpose;
- hardware/OS/release manifest;
- network endpoints как optional transport, не identity;
- public node keys и certificate signing request;
- supported protocol/event/policy versions;
- capabilities: склад, clearing, audit, relay и другие;
- запрашиваемые limits и data scopes;
- named responsible people и recovery contacts;
- backup/restore evidence;
- security/threat questionnaire;
- sponsor и proposed trust expiry;
- node bond/insurance/reserve, если policy требует.

Private key никогда не покидает узел.

## Проверка организации и людей

Registrar проверяет существование owner, полномочия подписантов, related
parties, conflicts, технических хранителей, процесс смерти/недееспособности и
доступность независимого аудитора. Один technical admin недостаточен для
получения clearing/storage capabilities.

## Technical challenge

Онлайн или через съёмный носитель:

1. Registrar выдаёт nonce/challenge package.
2. Узел подписывает nonce node key и отдельной подписью ответственного человека.
3. Возвращает release manifest, capability statement и integrity report.
4. Registrar проверяет signatures, clock range и отсутствие replay.
5. Узел импортирует test package и возвращает signed receipt.
6. Выполняется backup/restore или предоставляется свежий audit evidence.

Challenge доказывает владение ключом и совместимость, но не добросовестность
организации.

## Trust contract

Certificate/contract фиксирует:

- node/owner/sponsor;
- public keys и validity;
- status/trust level;
- разрешённые capabilities/event types;
- inbound/outbound data scopes;
- per-period quantity/value/exposure limits;
- allowed counterparties/territory;
- maximum offline epoch;
- required policy versions;
- audit/backup/update SLA;
- incident notification SLA;
- node liability limit/bond references;
- expiry, renewal и revocation conditions.

Peer может установить более низкий bilateral limit, чем federation maximum.

## Первое подключение

1. Импортировать signed trust package.
2. Добавить certificate chain и current revocation lists.
3. Обменяться capability/checkpoint packages.
4. Выполнить test events без хозяйственного эффекта.
5. Активировать limited bilateral channel двумя сторонами.
6. Провести первый малый хозяйственный package с manual approval.
7. Выполнить reconciliation и подписать receipt.

Автоматическая постоянная network connection не обязательна. Те же правила
работают для offline media.

## Управление узлом в GUI

Карточка внешнего узла:

- owner/sponsor и named responsible people;
- status, trust level, capabilities;
- certificates/keys и expiry;
- bilateral limits и текущая exposure;
- protocol/policy compatibility;
- last sync/checkpoint и sequence gaps;
- audits, backups и update posture;
- incidents/conflicts;
- bond/reserve и liability cases;
- actions: limit, suspend, quarantine, rotate, renew, revoke.

## API

```text
POST /nodes/applications
POST /nodes/applications/{id}/submit
POST /nodes/applications/{id}/challenge
POST /nodes/applications/{id}/challenge-response
POST /nodes/applications/{id}/audit-decision
POST /nodes/{id}/activate-limited
POST /nodes/{id}/activate
GET  /nodes/{id}/trust-contract
POST /nodes/{id}/bilateral-limits
POST /nodes/{id}/certificates/rotate
POST /nodes/{id}/renew
POST /nodes/{id}/suspend
POST /nodes/{id}/quarantine
POST /nodes/{id}/revoke
GET  /nodes/{id}/exposure
GET  /nodes/{id}/responsibility
```

## Renewal и отключение

До expiry проверяются owner/roles, keys, release, protocol, incidents, audit,
restore evidence, limits и unresolved conflicts. Expired trust запрещает новые
packages, но не стирает историю и старые proofs.

Planned disconnect закрывает новые operations, обменивается final checkpoints,
reconciles obligations и сохраняет verification keys. Emergency revoke
распространяет signed package и помещает незавершённые операции в review.

## Acceptance

- узел нельзя активировать без owner и named responsible people;
- private key не передаётся registrar;
- capability/limit невозможно увеличить одной стороной задним числом;
- unknown policy/protocol блокирует хозяйственный package;
- expired/revoked key не создаёт новую accepted operation;
- node quarantine сохраняет local history и доказательства;
- disconnect не оставляет неразобранную bilateral exposure;
- весь onboarding воспроизводим из signed events и документов.
