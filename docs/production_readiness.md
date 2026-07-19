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
- [ ] Protected amount и solidarity contour недоступны взысканию.
- [ ] Каждая critical command имеет actor/role/scope/evidence/exposure.
- [ ] Appeal и compensation проверены end-to-end.
- [ ] Admin console разделяет User, Member, Membership, Organization и Node.
- [ ] Клиринговый cycle проходит freeze/preview/dispute/finalize/reconcile.
- [ ] Clearing proof и participant statements воспроизводимы.
- [ ] Active external node имеет owner, named roles и действующий trust contract.
- [ ] Bilateral node limits и node bond ограничивают внешнюю exposure.
- [ ] Quarantine, rehabilitation и disconnect узла проверены end-to-end.
- [ ] Federated offer search проверяет подпись, home node и freshness.
- [ ] Landed cost воспроизводима, а estimated logistics явно отделена.
- [ ] Goods/logistics reservation saga имеет expiry и компенсации.
- [ ] Inter-node prepare не превышает bilateral exposure.
- [ ] Commit certificate требует approvals всех affected home nodes.
- [ ] Pending local apply и reconciliation восстановлены после сбоя.

## Security

- [ ] Threat model и independent security review завершены.
- [ ] Production keys сгенерированы и разделены по назначению.
- [ ] Private keys/secrets отсутствуют в Git/images/plain backup.
- [ ] Local auth, revoke, step-up и break-glass протестированы.
- [ ] Release/package/event signatures имеют independent test vectors.
- [ ] Critical/high findings закрыты или formal accepted risk подписан.
- [ ] Incident drill key compromise выполнен.

## Resilience

- [ ] Узел устанавливается без Интернета и публичного registry.
- [ ] Полный restore на резервном оборудовании укладывается в RTO.
- [ ] RPO подтверждён измерением и сверкой событий.
- [ ] Backup включает DB, blobs, manifest, trust data и release.
- [ ] Update, interrupted update и rollback испытаны.
- [ ] Paper forms и последующий ввод испытаны.
- [ ] Offline split/sync/conflict drill завершён.
- [ ] Работа при потере broker/federation не блокирует local critical path.

## Quality

- [ ] CI release gates зелёные на конкретном commit.
- [ ] Migration с предыдущего production release проверена.
- [ ] Clearing golden/property/permutation tests зелёные.
- [ ] Concurrency tests выполнены многократно.
- [ ] OpenAPI compatibility report принят.
- [ ] Browser/device/accessibility matrix пройдена.
- [ ] Capacity test выполнен на минимальном host.
- [ ] Нет flaky critical tests.

## Operations

- [ ] Назначены оператор, security admin, backup custodian и on-call contacts.
- [ ] Dashboards/alerts работают без внешнего Интернета.
- [ ] Runbooks доступны локально и на бумаге.
- [ ] Clock, disk, UPS и certificate monitoring работают.
- [ ] Support и escalation обучены без доступа к production secrets.
- [ ] Диагностический bundle проверен на отсутствие PII/secrets.
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
