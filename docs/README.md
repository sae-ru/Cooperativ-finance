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
- [Подписанный офлайн-релиз](release_runbook.md)
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
- [Реализованный Slice 1](implemented_slice_1.md)
- [Реализованный Slice 2](implemented_slice_2.md)
- [Реализованный Slice 3](implemented_slice_3.md)
- [Реализованный Slice 4](implemented_slice_4.md)
- [Реализованный Slice 5](implemented_slice_5.md)
- [Реализованный Slice 6](implemented_slice_6.md)
- [Реализованный Slice 7](implemented_slice_7.md)
- [Реализованный Slice 8](implemented_slice_8.md)
- [Реализованный Slice 9](implemented_slice_9.md)
- [Реализованный Slice 10](implemented_slice_10.md)
- [Реализованный Slice 11](implemented_slice_11.md)
- [Инженерный baseline Slice 12](implemented_slice_12.md)
## Начать здесь / Start here

- [Концепция / Concept](concept.md)
- [Руководство пользователя / User guide](user_guide.md)
- [Безопасность учётной записи / Account security](user-guide/account-security.md)
- [Установка / Deployment](deployment.md)
- [Архитектура / Architecture](architecture.md)
- [Интерфейс / Interface architecture](gui_architecture.md)
- [Языковые файлы / Language files](../lang/README.md)
- [Реализованный Slice 13](implemented_slice_13.md)
- [Реализованный Slice 14](implemented_slice_14.md)
- [Реализованный Slice 15](implemented_slice_15.md)
- [Реализованный Slice 16](implemented_slice_16.md)
- [Реализованный Slice 17](implemented_slice_17.md)
- [Реализованный Slice 18: кабинет пайщика и сквозной обмен](implemented_slice_18.md)
- [Реализованный Slice 19: объяснимая проверка аномалий](implemented_slice_19.md)
- [Реализованный Slice 20: локальная MFA и аварийный доступ](implemented_slice_20.md)
- [Реализованный Slice 21: полный версионированный антифрод-контур](implemented_slice_21.md)
- [Реализованный Slice 22: раздельный административный реестр](implemented_slice_22.md)
- [Реализованный Slice 23: безопасный ввод участников](implemented_slice_23.md)
- [Реализованный Slice 24: жизненный цикл внешних интеграций](implemented_slice_24.md)
- [Реализованный Slice 25: безопасное объединение дубликатов](implemented_slice_25.md)
- [Реализованный Slice 26: контролируемый выход и преемственность](implemented_slice_26.md)
- [Реализованный Slice 27: аварийная непрерывность физического хранения](implemented_slice_27.md)
- [Реализованный Slice 28: fail-closed production deployment](implemented_slice_28.md)
- [Реализованный Slice 29: локальная готовность узла и безопасная диагностика](implemented_slice_29.md)
- [Реализованный Slice 30: финальная компенсация из личного паевого резерва](implemented_slice_30.md)
- [Реализованный Slice 31: точная десятичная арифметика интерфейса](implemented_slice_31.md)
- [Реализованный Slice 32: сквозная прослеживаемость товарного исполнения](implemented_slice_32.md)
- [Реализованный Slice 33: exactly-once для хозяйственных активов](implemented_slice_33.md)
- [Реализованный Slice 34: подписанная цепочка ответственности](implemented_slice_34.md)
- [Реализованный Slice 35: ответственность за полномочия и физическую сохранность](implemented_slice_35.md)
- [Реализованный Slice 36: персональная ответственность за роли](implemented_slice_36.md)
- [Реализованный Slice 37: санкции и кризисные решения](implemented_slice_37.md)
- [Реализованный Slice 38: ответственность внешнего узла](implemented_slice_38.md)
- [Реализованный Slice 39: платформенный контракт офлайн-релиза](implemented_slice_39.md)
- [Реализованный Slice 40: проверяемое отсутствие открытых секретов](implemented_slice_40.md)
- [Реализованный Slice 41: атомарное событие и outbox](implemented_slice_41.md)
- [Реализованный Slice 42: безопасные офлайн-черновики браузера](implemented_slice_42.md)
- [Реализованный Slice 43: проверяемое полное восстановление](implemented_slice_43.md)
- [Реализованный Slice 44: подписанная совместимость и rollback](implemented_slice_44.md)
- [Реализованный Slice 45: сквозная ответственность адресной книги](implemented_slice_45.md)
- [Реализованный Slice 46: локальная наблюдаемость без Интернета](implemented_slice_46.md)
- [Инструкция: локальные офлайн-черновики](user-guide/offline-drafts.md)
- [Шаблоны внешних evidence](evidence_templates/production_readiness_decision.md)
- [Production readiness](production_readiness.md)
- [Правила разработки с ИИ](ai_development_rules.md)
- [Стандарты кодирования](coding_standards.md)
- [Трассировка требований](requirements_traceability.md)
- [Открытые решения](open_decisions.md)

## Правило актуальности

Изменение инварианта, формата подписи, предела ответственности или правила
конфликта требует ADR, миграции или новой версии протокола и теста. История
решений не переписывается задним числом.

## Актуальное инженерное дополнение Slice 45

- [Сквозная ответственность адресной книги](implemented_slice_45.md)

## Актуальное инженерное дополнение Slice 46

- [Локальная наблюдаемость без Интернета](implemented_slice_46.md)