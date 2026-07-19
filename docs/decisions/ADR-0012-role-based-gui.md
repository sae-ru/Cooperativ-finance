# ADR-0012: Role-based GUI

Статус: Accepted.

## Context

Универсальное меню перегружает пайщика и повышает риск ошибочной критической
операции. Разные роли работают на разных устройствах.

## Decision

GUI строится как рабочее место по роли с общей страницей объекта, task inbox,
явной responsibility/exposure и role-specific desktop/mobile flows.

## Consequences

Нужны разные navigation/read models и E2E по ролям. Скрытие пункта не заменяет
server authorization.

## Validation

Role usability tests, authorization matrix, viewport/accessibility suite.
