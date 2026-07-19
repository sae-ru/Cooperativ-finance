# Правила разработки с ИИ

Статус: обязательный процесс для автоматизированных инструментов разработки.

## Перед изменением

1. Прочитать ТЗ, `docs/README.md`, архитектуру и документ затронутого модуля.
2. Проверить ADR и open decisions.
3. Найти владельца таблиц, событий и policy.
4. Зафиксировать изменяемый invariant и threat impact.
5. Не начинать feature, если обязательное хозяйственное решение отсутствует.

## Во время реализации

- следовать существующим module boundaries;
- не переносить business logic в route/React component;
- изменять state только через application use case;
- добавлять migration, event schema, audit, idempotency и permissions вместе с
  critical command;
- использовать Decimal/Quantity, UTC и явные units;
- не создавать собственную криптографию;
- не добавлять cloud/CDN dependency в critical path;
- не копировать исходный код из сторонних источников;
- не изменять чужие unrelated files и generated artifacts без необходимости;
- не скрывать неутверждённую policy значением по умолчанию.

## Обязательный результат задачи

- implementation;
- focused tests и при необходимости broader suite;
- migration/recovery impact;
- обновлённый OpenAPI/event schema;
- docs/ADR при изменении решения;
- проверка security/privacy/offline impact;
- короткий отчёт с фактически выполненными командами и residual risk.

## Запрещено агенту

- считать собственный текст юридическим утверждением;
- финализировать open decision без владельца;
- снижать test/security requirement ради зелёного build;
- удалять signed/audit history;
- ослаблять роль, лимит или двойной контроль без ADR и policy;
- автоматически исправлять production data ad hoc SQL;
- выдавать demo за production-ready;
- публиковать private key, token, PII или whistleblower data.

## Независимость реализации

Код, tests, comments и структура проекта создаются самостоятельно на основании
утверждённого ТЗ и ADR. Для каждой dependency фиксируются версия, происхождение
и лицензия; совместимость лицензий проверяется до включения в поставку.

## Самопроверка

Перед завершением агент отвечает:

- какой invariant защищён;
- может ли операция выполниться дважды;
- что произойдёт при concurrency и retry;
- кто несёт ответственность и где это видно;
- что попадёт в signed event/audit;
- как feature работает без Интернета;
- как восстановить данные;
- какие PII/secrets затронуты;
- какие тесты доказывают результат.
