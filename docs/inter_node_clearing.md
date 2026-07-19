# Межузловой клиринг

Статус: production-архитектура post-pilot federation contour.

## Назначение

Межузловой клиринг уменьшает подтверждённые обязательства между участниками
разных узлов. Он не объединяет базы данных, не передаёт региональному серверу
право единолично менять локальные позиции и не требует постоянной связи.

Локальный клиринг должен быть доказан пилотом до включения этого контура.

## Участники

- home node каждого обязательства;
- buyer/seller/member organizations;
- clearing coordinator, который строит proposal, но не определяет истину;
- risk controllers affected nodes;
- federation registrar/trust contracts;
- auditors/verifiers.

Coordinator может быть региональным узлом или выбранным участником цикла. Его
отказ не уничтожает исходные obligations; цикл может быть перестроен другим
координатором из тех же signed snapshots.

## Объекты

- `FederatedClearingMembership`;
- `InterNodeObligationRef`;
- `NodePosition`;
- `InterNodeClearingPolicy`;
- `FederatedInputSnapshot`;
- `NodePrepareReceipt`;
- `FederatedClearingProposal`;
- `NodeClearingApproval`;
- `CommitCertificate`;
- `NodeApplyReceipt`;
- `FederatedClearingProof`;
- `FederatedClearingConflict`.

## Допуск обязательства

Обязательство включается, если:

- имеет глобальный id, home node и version;
- подтверждено обеими сторонами либо предусмотренным доказательством;
- не disputed/frozen/defaulted вне policy;
- valuation/unit совместимы;
- home и counterparty nodes имеют действующий bilateral trust;
- внешняя exposure остаётся в limits;
- guarantee/collateral условия выполнены;
- protocol/policy/algorithm versions поддерживаются всеми affected nodes.

## Жизненный цикл

```text
DRAFT
 -> COLLECTING_SNAPSHOTS
 -> PREPARING_NODES
 -> PREPARED
 -> PROPOSED
 -> VERIFYING
 -> COMMIT_CERTIFIED
 -> APPLYING
 -> RECONCILED
```

Дополнительные: `PREPARE_EXPIRED`, `REJECTED`, `CONFLICT`,
`COMMITTED_PENDING_APPLY`, `CANCELLED`.

## Фаза 1. Signed snapshots

Каждый home node формирует минимальный snapshot допустимых obligations и своей
external position. Snapshot содержит versions, remaining values, disputes,
limits, node checkpoint, policy ids и signature. Coordinator не получает право
изменять payload.

## Фаза 2. Prepare

Coordinator рассылает ordered input hash affected nodes. Каждый node:

1. Проверяет trust, policy, obligations и versions.
2. Транзакционно резервирует включённые остатки и external exposure.
3. Замораживает конфликтующие локальные изменения до prepare expiry.
4. Возвращает signed `NodePrepareReceipt` с snapshot hash, reservations,
   maximum exposure и expiry.

Prepare receipt не финализирует клиринг. Timeout без commit certificate
освобождает reservations отдельным событием.

## Фаза 3. Proposal

После получения всех required prepare receipts coordinator запускает чистый
детерминированный algorithm на canonical federated input. Proposal содержит
positions before/after, entries, exclusions, residues, node limits,
algorithm/input/result hashes и prepare receipt hashes.

Coordinator не может добавить obligation, которого нет в signed snapshot, или
превысить prepared amount.

## Фаза 4. Verify and accept

Каждый affected home node независимо:

- воспроизводит result hash;
- проверяет только допустимое раскрытие чужих данных;
- проверяет свои entries и aggregate exposure;
- проверяет все prepare receipts и expiry;
- открывает dispute или подписывает `NodeClearingApproval`.

Для финальности нужны подписи всех home nodes, чьи obligations изменяются.
Quorum не может обязать несогласный узел. Coordinator может выделить отдельный
замкнутый component и предложить новый cycle только его участникам.

## Фаза 5. Commit certificate

Coordinator собирает approvals в `CommitCertificate`, покрывающий cycle,
input/result hashes, prepare receipts, affected nodes, algorithm/policy versions
и signatures. Certificate означает, что все затронутые узлы заранее приняли
один и тот же результат.

После появления валидного commit certificate prepare не может быть просто
отменён по timeout. Узел обязан применить certificate или перейти в
`COMMITTED_PENDING_APPLY` до восстановления.

## Фаза 6. Local apply

Каждый node применяет только свои entries одной локальной PostgreSQL-транзакцией:

- проверяет idempotency certificate;
- переводит prepared reservations в finalized clearing entries;
- уменьшает obligations;
- обновляет node positions/exposure;
- сохраняет proof/certificate;
- создаёт local events, audit, outbox;
- возвращает signed `NodeApplyReceipt`.

Это не распределённый 2PC: нет общей блокировки БД и глобального transaction
manager. Экономическая финальность задаётся commit certificate, а техническое
применение может догнать после восстановления узла.

## Фаза 7. Reconciliation

Цикл получает `RECONCILED`, когда собраны apply receipts всех affected nodes и
их hashes согласованы. До этого UI показывает lagging nodes. Повторный apply
идемпотентен.

Если receipt не получен в SLA, узел ограничивается для новых циклов, но
финальный certificate и история сохраняются.

## Риски и ответственность

- каждый node отвечает за корректность подписанного snapshot и local apply;
- business signers отвечают за исходные obligations;
- coordinator отвечает за неизменность сборки proposal, но не может навязать
  результат без approvals;
- release/algorithm owners отвечают за соответствие version/test vectors;
- bilateral limits и node bonds ограничивают внешний ущерб;
- sponsor/registrar не отвечает сверх явной guarantee и собственной доказанной
  procedural negligence;
- liability требует causal assessment и appeal.

Клиринг уменьшает встречные позиции, но не покрывает чистый внешний долг узла.
Оставшаяся position регулируется credit/guarantee/settlement policy.

## Отказоустойчивость

| Сбой | Результат |
|---|---|
| coordinator до commit | cycle отменяется после prepare expiry |
| node до approval | commit невозможен; возможен новый component |
| coordinator после certificate | любой участник распространяет certificate |
| node после approval до apply | `COMMITTED_PENDING_APPLY`, apply после recovery |
| conflicting certificate | reject, quarantine coordinator/keys, incident |
| expired trust до commit | новый certificate запрещён |
| revoked key с compromise time | affected cycle review по incident policy |

## Приватность

Узел получает полные данные только собственных obligations и минимальные
counterparty refs. Coordinator может работать с pseudonymous node/member ids и
aggregated edges. Proof поддерживает selective disclosure: общий result hash и
индивидуальные inclusion proofs.

## GUI

Региональное рабочее место показывает:

- participating nodes и trust status;
- snapshot/prepare readiness;
- positions и limits по разрешённому scope;
- proposal before/after;
- node approvals/disputes;
- commit certificate signatures;
- apply receipts и lagging nodes;
- proof verification и reconciliation.

Локальный оператор видит собственные entries, maximum external exposure и
кнопки prepare/approve/apply. Нельзя одной кнопкой подписать за другой node.

## API

```text
POST /federation/clearing/cycles
POST /federation/clearing/cycles/{id}/snapshots
POST /federation/clearing/cycles/{id}/prepare
POST /federation/clearing/cycles/{id}/prepare-receipts
POST /federation/clearing/cycles/{id}/proposal
POST /federation/clearing/cycles/{id}/verify
POST /federation/clearing/cycles/{id}/approvals
POST /federation/clearing/cycles/{id}/commit-certificate
POST /federation/clearing/cycles/{id}/apply
POST /federation/clearing/cycles/{id}/apply-receipts
GET  /federation/clearing/cycles/{id}/proof
POST /federation/clearing/proofs/verify
POST /federation/clearing/cycles/{id}/reconcile
```

## Acceptance

- coordinator не меняет signed snapshot;
- obligation без home node approval не клирится;
- prepare блокирует только ограниченный amount до expiry;
- proposal воспроизводим всеми узлами;
- commit certificate содержит подписи всех affected home nodes;
- один certificate не применяется дважды;
- node восстанавливает pending apply после перезапуска;
- coordinator loss после commit не блокирует распространение certificate;
- conflicting result/certificate обнаруживается и изолируется;
- aggregate external exposure не превышает bilateral limits;
- proof проверяется без общей БД;
- reconciliation обнаруживает отсутствующий apply receipt.
