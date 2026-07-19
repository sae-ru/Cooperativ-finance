# Индекс проектной документации

Этот каталог является навигационной точкой для разработки, эксплуатации,
аудита и восстановления контекста.

## Порядок чтения разработчика

1. [Архитектура](architecture.md)
2. [Границы модулей](module_boundaries.md)
3. [Доменная модель](domain_model.md)
4. [Модель данных](data_model.md)
5. [Транзакции и события](transaction_event_model.md)
6. [Каталог событий](event_catalog.md)
7. [API](api.md)
8. [Архитектура GUI](gui_architecture.md)
9. [План разработки](development_plan.md)
10. [Стратегия тестирования](testing_strategy.md)

## Архитектура и протоколы

- [Архитектура системы](architecture.md)
- [Границы модулей](module_boundaries.md)
- [Доменная модель](domain_model.md)
- [Модель данных](data_model.md)
- [Транзакции и события](transaction_event_model.md)
- [Каталог событий](event_catalog.md)
- [API и ошибки](api.md)
- [Офлайн-протокол](offline_protocol.md)
- [Клиринговый алгоритм](clearing_algorithm.md)
- [Операционный контур клиринга](clearing_operations.md)
- [Подключение внешнего узла](node_onboarding.md)
- [Ответственность внешнего узла](node_liability_policy.md)
- [Федеративный поиск и логистика](federated_catalog_search.md)
- [Межузловой клиринг](inter_node_clearing.md)
- [Изменение и разделение протокола](protocol_fork_policy.md)
- [ADR](decisions/README.md)

## Правила хозяйственной системы

- [Юридическая модель](legal_model.md)
- [Бухгалтерская совместимость](accounting_model.md)
- [Паи и ответственность](share_liability_model.md)
- [Связанные лица](related_parties_policy.md)
- [Солидарная помощь](solidarity_policy.md)
- [Контекстная надёжность](reputation_policy.md)
- [Санкции и апелляции](sanctions_appeals_policy.md)
- [Модель угроз](threat_model.md)
- [Безопасность и ключи](security.md)
- [Кризисный протокол](crisis_protocol.md)

## Интерфейс

- [Архитектура GUI](gui_architecture.md)
- [Административный интерфейс](admin_console.md)
- [Дизайн-система](design_system.md)
- [Пользовательские сценарии](user_scenarios.md)
- [Бумажные формы](paper_forms.md)

## Эксплуатация и разработка

- [Развёртывание](deployment.md)
- [Резервное копирование и восстановление](recovery_runbook.md)
- [Наблюдаемость](observability.md)
- [Стратегия тестирования](testing_strategy.md)
- [План разработки](development_plan.md)
- [Пилот](pilot_runbook.md)
- [Production readiness](production_readiness.md)
- [Правила разработки с ИИ](ai_development_rules.md)
- [Стандарты кодирования](coding_standards.md)
- [Трассировка требований](requirements_traceability.md)
- [Открытые решения](open_decisions.md)

## Правило актуальности

Изменение инварианта, формата подписи, предела ответственности или правила
конфликта требует ADR, миграции или новой версии протокола и теста. История
решений не переписывается задним числом.