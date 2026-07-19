# Стандарты кодирования

Статус: обязательные conventions первого production scaffold.

## Python

- Python 3.13.x, строгая фиксация toolchain и dependencies;
- полный type checking для application/domain/public contracts;
- async используется на I/O boundary, domain остаётся обычным синхронным кодом;
- один SQLAlchemy Session на application transaction;
- implicit autocommit и скрытые commits в repositories запрещены;
- Decimal создаётся из строк, не из float;
- timezone-naive datetime запрещён;
- domain errors являются типизированными и переводятся в API в одном месте;
- dataclass/Pydantic не используются как ORM entity одновременно;
- crypto/time/id generation доступны domain только через ports;
- side effect после commit идёт через outbox.

## SQLAlchemy и PostgreSQL

- ORM model находится в infrastructure модуля;
- eager/lazy loading выбирается явно, N+1 проверяется integration tests;
- критический update имеет expected version или conditional predicate;
- foreign keys, CHECK, unique и indexes создаются migration;
- raw SQL допустим для измеренного сложного запроса и имеет repository test;
- JSONB не заменяет нормальную схему для ключевых полей;
- названия constraints и indexes детерминированы;
- transaction retry находится в application boundary, не внутри domain action.

## API

- route тонкий: parse, authorize transport, call use case, map response;
- command DTO отличается от read DTO;
- server не принимает вычисляемые поля клиента как истину;
- create/transition route документирует idempotency;
- object-level authorization проверяется use case;
- response не возвращает ORM object напрямую;
- breaking OpenAPI change блокирует CI без новой версии/approval.

## TypeScript и React

- strict TypeScript, `any` требует локального обоснования;
- API types генерируются из принятого OpenAPI;
- TanStack Query keys централизованы по entities;
- server state не копируется в глобальный client store;
- Zod проверяет формы и offline drafts, но не заменяет server validation;
- business calculations/exposure не реализуются повторно в frontend;
- компоненты разделяются на page, feature, entity и shared boundaries;
- пользовательский текст берётся из i18next, технические codes не показываются;
- mutation учитывает timeout/unknown result через idempotency lookup;
- IndexedDB adapter не импортируется domain-facing UI напрямую.

## Именование

- domain names совпадают в ТЗ, Python, API и UI glossary;
- команды: imperative (`IssueCommodityRight`);
- события: past tense (`commodity_right_issued`);
- queries: предмет результата (`GetAvailableInventory`);
- boolean не называется расплывчато `flag`;
- status enum не переиспользуется между разными aggregates;
- `amount`, `quantity`, `balance`, `valuation` не являются синонимами.

## Комментарии и документация

Комментарий объясняет причину, invariant или нетривиальную concurrency/security
границу. Он не пересказывает строку кода. Публичный module contract имеет
короткое описание; экономическая policy ссылается на versioned документ/ADR.

## Ошибки и логирование

- исключение не используется как обычный успешный branch domain state;
- domain error имеет stable code;
- unexpected exception получает request id и redacted log;
- PII/secrets не интерполируются в log;
- retryable error явно классифицируется;
- catch-all не превращает partial failure в success.

## Тесты рядом с кодом

- domain test на каждый transition и запрет;
- repository test на constraint/lock/query;
- application test на atomic event/outbox/audit;
- API test на auth/idempotency/error envelope;
- UI test на states и user action;
- regression test именуется по поведению, а не номеру бага;
- production defect fixture очищается от PII.

## Изменение схемы или протокола

PR обязан включать migration, compatibility note, rollback/recovery, event/API
schema, fixtures и docs. Изменение signed canonical bytes без новой версии
запрещено.
