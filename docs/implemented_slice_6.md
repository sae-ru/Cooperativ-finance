# Реализованный Slice 6: паи и ограниченный риск

Статус: реализовано и проверено на Linux-контейнерах с PostgreSQL. Срез
добавляет явные паевые контуры, защищённую часть пая, персонально принимаемые
резервы риска, связанные группы и доказуемую оценку ответственности.

Срез намеренно не выполняет автоматическое взыскание. Оценённый ущерб
фиксируется со статусом `NOT_EXECUTED`; движение пая, апелляция, санкции и
репутационная проекция требуют последующих утверждённых правил.

## Политика лимитов

Политика риска является версионированным агрегатом с неизменяемым canonical
payload и hash `sha256:<64 lowercase hex>`. Она задаёт:

- единицу паевого учёта;
- максимальный exposure одного участника;
- максимальный exposure связанной группы;
- предельную глубину цепочки поручительств;
- правило защищённой суммы;
- правило связанных лиц.

`COOPERATIVE_ADMIN` предлагает политику с решением органа и READY evidence.
Другой активный участник с ролью `RISK_ADMIN` или `AUDITOR` утверждает точный
hash. Самоутверждение запрещено. Новая активная политика переводит предыдущую
в `SUPERSEDED` отдельным signed event, не переписывая её историю.

## Паевые счета

Счёт открывается по активной политике и навсегда сохраняет
`opening_policy_id`. Поддержаны контуры:

- `PRIMARY`: основной пай, не используемый как автоматическое обеспечение;
- `GUARANTEE`: прямой риск, поручительство и кредитный лимит;
- `ROLE`: ответственность за именованное назначение роли;
- `SOLIDARITY`: отдельный контур, не используемый для коммерческого exposure.

Для каждого счёта сервер и БД защищают инвариант:

```text
balance >= protected_amount + executed_not_settled
```

Взносы являются append-only записями `risk.share_contributions`. Runtime role
не имеет `UPDATE` и `DELETE`; дополнительный trigger блокирует изменение даже
при ошибочно расширенных правах.

Доступный остаток рассчитывается как:

```text
available =
  balance
  - protected_amount
  - executed_not_settled
  - sum(active amount_reserved)
```

## Резерв риска и поручительство

Поддержаны `DIRECT_OBLIGATION`, `GUARANTEE`, `CREDIT_LIMIT` и `ROLE_BOND`.
Предложение содержит конкретный risk id, стороны, резерв, `max_loss`, долю
покрытия, срок, условия срабатывания, освобождения и исключения. Эти условия
канонизируются и получают hash.

До создания предложения API возвращает preview:

- доступный остаток счёта до и после;
- exposure владельца до и после;
- exposure всей связанной группы до и после;
- применённые лимиты и стабильный reason code.

Предложить риск может `RISK_ADMIN` или `AUDITOR`, но активным он становится
только после личного подтверждения владельцем конкретного счёта точного
`terms_hash` и версии. Подтверждение повторно проверяет лимиты под PostgreSQL
advisory lock. Два конкурентных подтверждения последнего остатка не могут
создать перерасход.

Поручительство требует трёх разных активных участников: поручителя, должника и
бенефициара. Сервер строит граф активных поручительств и запрещает цикл либо
глубину сверх политики.

## Связанные лица

`RISK_ADMIN` предлагает связь `HOUSEHOLD`, `CONTROL` или `RELATED` с
доказательством. Независимый `RISK_ADMIN`/`AUDITOR`, не являющийся
инициатором или стороной связи, утверждает либо отклоняет её.

Лимит применяется к связной компоненте графа, а не только к одной паре.
Утверждение новой связи сериализуется по кооперативу и отклоняется, если
объединённая группа уже превысила policy limit.

## Случай ответственности

Случай можно открыть только для `ACTIVE` или `RELEASED` commitment. Оператор
не может открыть случай против собственного пая. Запись содержит уникальную в
кооперативе ссылку инцидента, факты, причинную схему, affected amount и READY
evidence.

Независимый проверяющий фиксирует:

- `FORCE_MAJEURE`, `GOOD_FAITH_ERROR`, `NEGLIGENCE`,
  `GROSS_NEGLIGENCE`, `INTENT` или `COLLUSION`;
- оценённый ущерб;
- мотивировку;
- coverage summary;
- срок обжалования.

Сумма всех оценок по commitment не может превысить его `max_loss`.
`protected_amount` не включается в доступное покрытие. Оценка не меняет баланс
счёта и не создаёт скрытого взыскания.

## Роли и видимость

- участник видит собственные счета и commitments, где он владелец, должник
  либо бенефициар;
- `COOPERATIVE_ADMIN` и `RISK_ADMIN` видят свой scope;
- глобальные `AUDITOR` и `SECURITY_ADMIN` имеют чтение локального узла;
- preview разрешён только владельцу счёта либо оператору риска;
- критическая команда повторно проверяет активные Member, Membership и role
  assignment, а не доверяет старой сессии.

GUI «Риск и паи» содержит сводку доступного остатка, политики, счета и
append-only взносы, preview и принятие commitment, граф связанности, случаи и
независимую оценку. Недопустимые команды скрыты, но окончательное решение
всегда принимает backend.

## Хранение и миграция

Revision `0008_bounded_risk_vertical_flow` создаёт в существующей схеме `risk`:

- `risk_policies`;
- `share_accounts`;
- `share_contributions`;
- `related_party_links`;
- `exposure_commitments`;
- `liability_cases`.

Все хозяйственные команды атомарно записывают state, signed event, audit,
outbox и idempotency result. Downgrade ниже `0008` останавливается при наличии
любых данных Slice 6; сама схема `risk` не удаляется, поскольку в ней уже
находятся таблицы прежнего контура ответственности.

## API

Чтение:

```text
GET /api/v1/risk/policies
GET /api/v1/risk/accounts
GET /api/v1/risk/accounts/{account_id}/contributions
GET /api/v1/risk/related-links
GET /api/v1/risk/commitments
GET /api/v1/risk/liability-cases
```

Команды:

```text
POST /api/v1/risk/exposure-previews
POST /api/v1/risk/policies
POST /api/v1/risk/policies/{policy_id}/approval
POST /api/v1/risk/accounts
POST /api/v1/risk/accounts/{account_id}/contributions
POST /api/v1/risk/related-links
POST /api/v1/risk/related-links/{link_id}/decision
POST /api/v1/risk/commitments
POST /api/v1/risk/commitments/{commitment_id}/acceptance
POST /api/v1/risk/commitments/{commitment_id}/release
POST /api/v1/risk/liability-cases
POST /api/v1/risk/liability-cases/{case_id}/assessment
```

Все state-changing endpoints требуют `Idempotency-Key`; подтверждения и
переходы изменяемых агрегатов требуют точную ожидаемую версию.

## Демоданные

Идемпотентный seed создаёт активную политику `DEMO_SHARE`, гарантийный счёт
Анны с балансом `100` и защищённой суммой `40`, а также лично принятый резерв
`30` с `max_loss=25` и coverage ratio `0.833333`. Liability case намеренно не
создаётся: демо показывает нормальное активное обязательство без исполнения.

Повторный seed сохраняет один policy, один account, один commitment и прежнюю
длину signed journal.

## Проверка

- backend: Ruff, strict mypy по 124 source files и 65 Pytest на PostgreSQL;
- backend coverage: 79.88% при обязательном пороге 75%;
- frontend: strict TypeScript, production PWA build и 57 Vitest;
- frontend coverage: 90.07% statements, 71.82% branches, 87.07% functions и
  93.40% lines;
- миграции: upgrade до `0008`, `alembic check`, downgrade `0008 -> 0007`,
  повторный upgrade и destructive downgrade guard;
- конкуренция: два одновременных acceptance последнего остатка дают ровно
  один `ACTIVE` commitment;
- журнал: risk flow включён в независимую проверку hash-chain и Ed25519.
