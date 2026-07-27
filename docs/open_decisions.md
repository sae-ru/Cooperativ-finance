# Реестр открытых решений

Статус: blocking register. Решение не закрывается кодом или default value.

Статусы: `OPEN`, `BASELINE_REVIEW`, `APPROVED`, `REJECTED`, `SUPERSEDED`.

| ID | Решение | Владелец | Блокирует | Статус |
|---|---|---|---|---|
| OD-001 | Пилотная юрисдикция и форма участников | Legal/Product | реальные данные и pilot | OPEN |
| OD-002 | Пилотные товары и услуги | Product/Cooperative | inventory slice | OPEN |
| OD-003 | Единицы, precision и rounding | Product/Accounting | data migrations | OPEN |
| OD-004 | Корзина клиринговой оценки | Economics/Accounting | valuation/clearing | OPEN |
| OD-005 | Максимальные credit limits | Risk committee | credit slice | OPEN |
| OD-006 | Требования к guarantee/reserve | Legal/Risk | guarantees | OPEN |
| OD-007 | Минимальные материальные резервы | Crisis group | reserves | OPEN |
| OD-008 | Состав кризисной группы | Governance | crisis activation | OPEN |
| OD-009 | Правила rationing | Governance/Legal | rationing | OPEN |
| OD-010 | Допустимый период автономии | Operations/Risk | offline epoch | OPEN |
| OD-011 | Аппаратный комплект узла | Operations | deployment qualification | OPEN |
| OD-012 | Алгоритмы подписи/шифрования | Security/Legal | signed journal | BASELINE_REVIEW |
| OD-013 | Точный профиль canonical JSON | Architecture/Security | signed journal | OPEN |
| OD-014 | Срок ключей и offline revocation SLA | Security/Governance | node trust | OPEN |
| OD-015 | Правила каждого sync conflict class | Domain/Governance | package apply | OPEN |
| OD-016 | Компенсация добросовестной стороне | Legal/Risk | arbitration | OPEN |
| OD-017 | Итоговые RPO/RTO | Operations/Governance | production readiness | BASELINE_REVIEW |
| OD-018 | Retention персональных/хозяйственных данных | Legal/Privacy | storage/backup | OPEN |
| OD-019 | Правила выхода участника | Legal/Cooperative | membership exit | OPEN |
| OD-020 | Условия остановки/расширения пилота | Product/Audit | pilot | OPEN |
| OD-021 | Юридическая модель паевых контуров | Legal/Accounting | shares | OPEN |
| OD-022 | Protected amount и max role bond | Legal/Risk | liability execution | OPEN |
| OD-023 | Aggregate exposure участника/household/related | Risk/Privacy | guarantees | OPEN |
| OD-024 | Coverage waterfall и additional contribution | Legal/Risk | liability execution | OPEN |
| OD-025 | Классификатор fault | Legal/Audit | sanctions/liability | OPEN |
| OD-026 | Формы и критерии solidarity aid | Fund governance | solidarity | OPEN |
| OD-027 | Residue/refund/audit campaign rules | Fund/Accounting | campaign close | OPEN |
| OD-028 | Минимальное раскрытие recipient/witness/whistleblower | Privacy/Legal | sensitive cases | OPEN |
| OD-029 | Reputation measurements/formula/decay/confidence | Audit/Governance | profile effects | OPEN |
| OD-030 | Шкала sanctions и emergency measures | Governance/Legal | sanctions | OPEN |
| OD-031 | Независимая appeal group и SLA | Governance | final sanctions | OPEN |
| OD-032 | Rehabilitation conditions | Governance/Audit | role restoration | OPEN |
| OD-033 | Death/incapacity/custody succession | Legal/Operations | continuity | OPEN |
| OD-034 | Related-party and identity continuity rules | Risk/Privacy | antifraud | OPEN |
| OD-035 | Межузловой mutual insurance | Federation/Legal | regional risk | OPEN |
| OD-036 | Signed update, rollback и protocol fork mandate | Governance/Security | production updates | BASELINE_REVIEW |
| OD-037 | Accounting/tax of free contributions and aid | Accounting/Tax | real solidarity | OPEN |

| OD-038 | Идентификация, duplicate merge и succession клиентов | Identity/Legal | admin console | OPEN |
| OD-039 | Registrar/sponsor и процедура node admission | Federation/Governance | node onboarding | OPEN |
| OD-040 | Node trust levels, capabilities и bilateral limits | Federation/Risk | external operations | OPEN |
| OD-041 | Юридическая модель node bond/exposure/protected amount | Legal/Risk | node liability | OPEN |
| OD-042 | Ответственность owner/sponsor/registrar/roles | Legal/Governance | node incidents | OPEN |
| OD-043 | Clearing dual-control thresholds и dispute SLA | Clearing governance | cycle finalize | OPEN |
| OD-044 | Node renewal, rehabilitation и disconnect reconciliation | Federation/Audit | node lifecycle | OPEN |
| OD-045 | Federated product/unit/quality/substitute mapping | Catalog/Product | federated search | OPEN |
| OD-046 | Offer quantity/geography/freshness/privacy publication | Federation/Privacy | offer index | OPEN |
| OD-047 | Landed-cost components, valuation conversion and fees | Accounting/Logistics | ranking | OPEN |
| OD-048 | Ranking versions, weights and crisis priorities | Product/Governance | search order | OPEN |
| OD-049 | Goods/logistics reservation saga expiry and compensation | Exchange/Logistics | cross-node purchase | OPEN |
| OD-050 | Federated clearing eligibility, prepare expiry and disclosure | Clearing/Risk | prepare | OPEN |
| OD-051 | Required signatures and finality of commit certificate | Legal/Federation | inter-node finalize | OPEN |
| OD-052 | Settlement of external net position and node default | Risk/Legal | external exposure | OPEN |
## Закрытие решения

Для `APPROVED` нужны:

- документ policy и версия;
- владелец и подписанты;
- дата действия и порядок перехода;
- legal/accounting/security review по применимости;
- mapping на affected modules/events/API/UI;
- test cases;
- миграция/rollback при необходимости;
- ADR, если меняется архитектурный выбор.

## Технические baseline

`BASELINE_REVIEW` означает, что архитектура предложила безопасную исходную
конфигурацию, но она не стала организационным решением. Текущие baseline:

- OD-012: Ed25519, SHA-256, Argon2id, standard AEAD;
- OD-017: local RPO 15 min, catastrophic RPO 24 h, RTO 4 h;
- OD-036: signed offline bundle, expand/contract migrations, tested rollback.

### OD-038 update после Slice 25

Внутрикооперативное объединение чистого подтверждённого дубля реализовано на code-level с immutable mapping и dual control. Решение остаётся `OPEN` для переноса экономически используемой identity, cross-cooperative merge, смерти/недееспособности, наследования и юридически утверждённой rollback/recovery процедуры.

### OD-038 update после Slice 26

Slice 26 реализовал containment до экономической преемственности: мгновенную остановку доступа, независимое TOTP-решение, versioned rollback и сохранение всех экономических ссылок на исходной карточке. OD-038 остаётся `OPEN`: необходимо отдельно утвердить идентификацию наследника/представителя, порядок расчёта паёв и долгов, судьбу поручительств и активных сделок, межузловое признание, сроки хранения и основания окончательного `CLOSED`.
