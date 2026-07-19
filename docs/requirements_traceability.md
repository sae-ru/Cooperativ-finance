# Трассировка требований

Статус: карта от ТЗ к проектированию и implementation slices.

| Раздел ТЗ | Основной документ | Slice | Основное доказательство |
|---|---|---|---|
| 0-3 Назначение/границы | `README`, `legal_model` | 0 | approved scope |
| 4 Устойчивость | `deployment`, `recovery_runbook` | 0, 11 | offline install/restore |
| 5 Роли | `domain_model`, `gui_architecture` | 1 | authorization matrix |
| 6 Бизнес-процессы | `user_scenarios` | 3-7 | E2E vertical flows |
| 7 Кредит/паи/помощь/репутация | профильные policies | 6, 8, 9 | property + policy tests |
| 8 Резервы | `crisis_protocol` | 10 | reserve drill |
| 9 Офлайн | `offline_protocol`, ADR-0005/0006 | 11 | split/sync drill |
| 10 Кризис | `crisis_protocol` | 10 | activate/review/close |
| 11 Доверие/безопасность | `security`, `threat_model` | 1-12 | security review/drills |
| 12 Объектная модель | `domain_model`, `data_model` | 1-11 | migrations/constraints |
| 13 Архитектура | `architecture`, ADR | 0 | architecture tests |
| 14 API/UI | `api`, `gui_architecture`, `design_system` | все | OpenAPI/E2E/a11y |
| 15 NFR | deployment/observability/recovery | 0, 11, 12 | capacity and RTO/RPO |
| 16 Тестирование | `testing_strategy` | все | release gates |
| 17 Приёмка | `production_readiness` | 12 | evidence pack |
| 18 Пилот | `pilot_runbook` | 12 | six-month report |
| 19 Этапы | `development_plan` | 0-12 | completed slices |
| 20 Репозиторий | `development_plan`, standards | 0 | scaffold layout |
| 21 Порядок кода | `development_plan` | 0-12 | backlog dependencies |
| 22 Документация/ИИ | `ai_development_rules` | все | PR checklist |
| 23 Бухгалтерия/право | `accounting_model`, `legal_model` | 5-12 | reconciliation/export |
| 24 Будущие модули | ADR до реализации | после 12 | отдельный scope |
| 25 Open decisions | `open_decisions` | до feature | approved policies |
| 26 Готовность | `production_readiness` | 12 | signed readiness review |

## Группы критериев приёмки

| Критерии | Покрываются |
|---|---|
| 1-5 | Identity slice, RBAC tests |
| 6-16 | Inventory/rights, DB concurrency tests |
| 17-27 | Deals/clearing, property and E2E tests |
| 28-37 | Offline protocol and field drills |
| 38-46 | Crisis/reserves policies and drills |
| 47-57 | Security/audit/recovery evidence |
| 58-74 | Operations, GUI, accounting and pilot metrics |
| 75-87 | Share liability property tests and legal policy |
| 88-96 | Responsibility/custody/conflict-of-interest E2E |
| 97-106 | Solidarity tests, privacy and fund reconciliation |
| 107-120 | Reputation/sanctions/appeals/protocol tests |
| 121-131 | Offline distribution, crypto, outbox, restore gates |
| 132-145 | Admin console and operational clearing lifecycle |
| 146-155 | Node onboarding, trust limits and bounded node liability |
| 156-166 | Federated offers, logistics quotes and landed-cost search |
| 167-175 | Inter-node prepare/commit/apply clearing protocol |

## Правило PR

Feature PR указывает затронутые разделы ТЗ, документы, open decisions,
acceptance criteria и tests. Если строка карты меняется, обновляется этот файл
или создаётся ADR. Наличие API endpoint без соответствующего доказательства не
считается реализацией требования.
