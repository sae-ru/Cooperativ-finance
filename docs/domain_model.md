# Доменная модель

Статус: базовая модель агрегатов и состояний.

## Общие value objects

| Тип | Состав | Правило |
|---|---|---|
| `EntityId` | UUID | генерируется приложением, не переиспользуется |
| `Quantity` | Decimal, unit, scale | float запрещён |
| `Valuation` | Decimal, unit, price_version | не является валютой по умолчанию |
| `TimeRange` | start, end | `end >= start` |
| `ActorRef` | person, organization, role_assignment | проверяется на момент действия |
| `EvidenceRef` | blob_id, hash, kind | содержимое проверяемо по хешу |
| `Exposure` | share_account, amount, reason, expiry | имеет предел и основание |
| `SignatureRef` | key_id, algorithm, signature | ключ действовал при подписи |

Все количества имеют единицу измерения. Время хранится в UTC.

## Основные агрегаты

### Identity

- `User`: способ входа, состояние доступа, сессии;
- `Member`: человек или организация в хозяйственной системе;
- `Cooperative`: локальная юридическая и организационная граница;
- `Membership`: участие и период действия;
- `RoleAssignment`: роль, scope, предел, назначившие, срок, статус.

### Assets

- `Product`, `UnitOfMeasure`, `Warehouse`;
- `InventoryLot`: физическая партия и её состояние;
- `QualityInspection`: измерения, доказательства и решение;
- `Reservation`: атомарная блокировка количества;
- `CommodityRight`: ограниченное право требования товара.

Состояния партии:

`DRAFT -> PENDING_ATTESTATION -> VERIFIED -> AVAILABLE -> DEPLETED`

Дополнительные: `QUARANTINED`, `DISPUTED`, `EXPIRED`, `RECALLED`.

Состояния права:

`ISSUED -> RESERVED | TRANSFERRED -> REDEEMED`

Дополнительные: `FROZEN`, `DISPUTED`, `EXPIRED`, `CANCELLED_BY_COMPENSATION`.

### Exchange

- `Deal`: согласованные сторонами условия;
- `Obligation`: конкретный долг исполнения, не обязательно денежный;
- `Fulfillment`: подтверждённая полная или частичная поставка;
- `LogisticsOrder`: цепочка физической передачи;
- `ClearingCycle`: вход, preview, окно спора, финальный результат;
- `ClearingProof`: воспроизводимое доказательство расчёта.

Изменение предмета подтверждённой сделки создаёт новую версию или
компенсирующую операцию.

### Risk

- `ShareAccount`: один из раздельных паевых контуров;
- `ShareReservation`: блокировка под конкретный риск;
- `Guarantee`: ограниченное поручительство;
- `CreditLimit`: предел отрицательной позиции;
- `RoleBond`: обеспечение критической роли;
- `LiabilityAssessment`: причинная связь, вина, ущерб, очередь покрытия;
- `ResponsibilityAssignment`: именованная ответственность;
- `CustodyTransfer`: передача физической сохранности.

Один `ShareReservation` связан ровно с одним основанием риска. Повторное
обеспечение возможно только в пределах явно рассчитанной общей экспозиции.

### Trust

- `Dispute`, `ArbitrationDecision`, `ReputationEvent`;
- контекстный `ReliabilityProfile`;
- `Sanction`, `Appeal`, `RehabilitationPlan`;
- `WhistleblowerReport`, `ConflictOfInterest`.

Репутационный профиль является вычисляемым представлением. Источником истины
служат проверяемые события и решения.

### Solidarity and Crisis

- `SolidarityFund`, `AidCampaign`, `Contribution`;
- `AidAllocation`, `AidDelivery`;
- `ReserveTarget`, `ReserveSnapshot`;
- `CrisisMandate`, `CrisisReview`, `RationingRule`, `RationingPlan`;
- `RationingAllocation`, `RationIssuance`, `CrisisPaperForm`, `CrisisReport`.

Помощь не создаёт `Obligation`, `CreditPosition` или положительный
`ReputationEvent` донора.

### Node

- `NodePassport`, `NodeCertificate`, `KeyRecord`;
- `OfflineEpoch`, `SignedEvent`;
- `SyncPackage`, `SyncInbox`, `SyncConflict`;
- `SecurityIncident`.

## Сквозная ответственность

Критическая команда содержит `performed_by`, `on_behalf_of`,
`role_assignment_id`, `scope`, `risk_exposure`, `evidence_refs`, обязательных
attesters/approvers и ожидаемого следующего ответственного.

Передача сохранности атомарно закрывает ответственность передающего и открывает
ответственность принимающего. Без подтверждения принимающего ответственным
остаётся передающий.

## Универсальные инварианты

- историческая подпись не удаляется;
- состояние не переходит назад прямым UPDATE;
- количество не становится отрицательным;
- резервирование не превышает доступный остаток;
- право не выпускается сверх подтверждённого обеспечения;
- право не погашается дважды;
- спорное исполнение не финализирует клиринг;
- роль и ключ действуют на момент команды;
- критическое действие не остаётся без физического лица;
- санкция и апелляция не финализируются одной группой людей;
- помощь не влияет на кредит, голос или репутацию;
- crisis capability не действует после expiry и не расширяется вне mandate scope;
- rationing confirm повторно проверяет frozen snapshot и общий verified остаток;
- базовая ration не создаёт обязательство, пай или reputation event;
- взыскание не превышает подтверждённую экспозицию.

## То, что не является агрегатом

- dashboard и отчёт: read model;
- текущий профиль репутации: проекция событий;
- доступный остаток: вычисляемое и транзакционно проверяемое значение;
- максимальная экспозиция: проекция резервирований и гарантий;
- sync package: транспортный контейнер, а не хозяйственный источник истины.

## Реализованные federation aggregates Slice 11

`ExternalNode` агрегирует паспорт, application, named responsibility, technical
challenge, audit status и active trust contract. `NodeTrustContract` ограничивает
срок, разрешённые события, bilateral limits, bond и exposure. `OfflineEpoch`
задаёт временную границу для `SyncPackage`, `SyncConflict`, `SyncReceipt` и
`FederationPaperForm`. `NodeSecurityIncident` и `NodeKeyRotationRequest`
изменяют допустимость будущих пакетов, не переписывая исторические подписи.

Aggregate transitions выполняются через services с advisory/row locks,
idempotency journal и signed node event в одной транзакции. Package simulation
не создаёт хозяйственного эффекта; apply разрешён только после успешной
проверки и закрытия blocking conflicts.
