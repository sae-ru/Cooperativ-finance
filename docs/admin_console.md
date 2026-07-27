# Административный интерфейс участников и организаций

Статус: обязательный production-контракт admin console.

## Термин «клиент»

В интерфейсе администратора «клиент» является поисковым представлением, но в
домене разделяется на:

- `User`: учётная запись входа;
- `Member`: физическое лицо или организация;
- `Cooperative`: организация/ячейка;
- `Membership`: участие member в cooperative;
- `RoleAssignment`: полномочия;
- `ServiceClient`: внешняя программная интеграция;
- `Node`: внешний или локальный технический узел.

Одна строка «клиента» не должна смешивать эти объекты и их ответственность.

## Административные роли

| Роль | Зона ответственности |
|---|---|
| Exchange participant | поиск товаров и оформление собственного обмена |
| Member registrar | заведение и проверка участника |
| Cooperative administrator | memberships и обычные роли своей организации |
| Data steward | исправление PII по процедуре, duplicate review |
| Risk administrator | limits только в пределах утверждённой policy |
| Security administrator | accounts, sessions, privileged access |
| Node registrar | внешние узлы и certificates |
| Auditor | read-only история всех административных действий |

«Суперадминистратор», способный единолично менять клиента, пай, роль, лимит,
ключ и историю, запрещён. Break-glass ограничен временем и аудитом.

## Навигация admin console

- Участники;
- Организации и членство;
- Учётные записи и сессии;
- Безопасность входа, recovery и временный аварийный доступ;
- Роли и полномочия;
- Паи, лимиты и поручительства;
- Связанные лица;
- Согласия и документы;
- Service clients;
- Узлы и certificates;
- Импорт/экспорт;
- Очередь проверок;
- Аудит и incidents.

## Карточка участника

Вкладки:

1. Обзор: status, identifiers, cooperative, contact visibility.
2. Memberships: история входа/выхода.
3. Accounts: user links, sessions, MFA, lock/recovery.
4. Roles: scope, period, assigners, bonds.
5. Shares and risk: раздельные контуры и active exposure.
6. Obligations: агрегированная безопасная сводка.
7. Reliability: только контекстные профили.
8. Documents/consents: versions и expiry.
9. Related parties: disclosed/confirmed/disputed links.
10. Audit: administrative and critical events.

Доступ к чувствительным вкладкам определяется отдельными scopes.

## Lifecycle участника

```text
APPLICANT -> PENDING_VERIFICATION -> LIMITED -> ACTIVE
          -> REJECTED
ACTIVE -> SUSPENDED -> ACTIVE
ACTIVE -> EXIT_PENDING -> CLOSED
ACTIVE -> DECEASED_OR_INCAPACITATED -> SUCCESSION_REVIEW
```

Admin action не перескакивает обязательную проверку. Closed member не удаляется
и не переиспользует identifier.

## Простой путь добавления фермера

В системе карточка человека и логин намеренно создаются разными ответственными пользователями.

1. `registrar` открывает **Участники**, создаёт карточку человека, проводит проверку до статуса **Активен** и добавляет членство.
2. `security` открывает **Доступ**, в блоке **Новая учетная запись** выбирает этого человека, задаёт логин и временный пароль.
3. Для обычного покупателя выбирается профиль **Искать и получать товары за паи**. Система сразу назначает базовую роль `EXCHANGE_PARTICIPANT`.
4. Для фермера-продавца выбирается профиль **Также предлагать свои товары**. Базовый доступ включается сразу, а право `NODE_BUSINESS_OPERATOR` ожидает независимого решения.
5. `auditor` открывает **Доступ** и одобряет запрос в блоке **Ожидают независимого решения**.
6. Фермер входит с временным паролем, заменяет его и сразу попадает в **Рынок**. В его меню остаются только **Рынок** и **Сделки**.

Техническая учетная запись создаётся без привязки к участнику и не получает никаких прав автоматически.
## Заведение клиента

1. Проверить duplicate по разрешённым identifiers.
2. Создать Member без автоматического full access.
3. Зафиксировать legal basis/consent и source document.
4. Создать Membership со статусом pending/limited.
5. Создать User отдельно, если нужен вход.
6. Назначить минимальную роль и срок.
7. Для privileged role потребовать независимый approval и role bond.
8. Отправить безопасную локальную activation procedure.
9. Создать signed administrative event.

## Изменение и блокировка

- PII correction хранит reason и предыдущий hash/доступную историю;
- role change создаёт новую assignment/version;
- suspension ограничивает новые действия, но не удаляет obligations;
- session revoke не равен исключению из cooperative;
- risk limit не редактируется через общую форму клиента;
- merge duplicates выполняется case с mapping старых identifiers, а не DELETE;
- смерть/недееспособность немедленно блокирует ключи и запускает succession.

## Bulk import

Импорт проходит staging, schema validation, duplicate report, dry run, approval
и chunked application. Строка с ошибкой не создаётся частично. Bulk import не
назначает privileged roles, limits или shares без отдельного workflow.

## Service clients

Для интеграции фиксируются owner organization, technical contact, scopes,
network allowlist, credentials/certificates, rate limits, expiry и audit. Один
service client не используется несколькими независимыми организациями.

## Безопасность учётной записи

Раздел **Безопасность** доступен каждому пользователю, а не только
администратору. Пользователь сам подключает/заменяет TOTP, видит срок текущего
step-up и подтверждает личность перед критической командой. Seed показывается
один раз как QR и ключ ручного ввода.

Контрольные роли дополнительно видят два отдельных workflow:

1. Recovery: один персональный сотрудник создаёт запрос с временным паролем и
   актом, другой независимо одобряет или отклоняет.
2. Break-glass: один сотрудник запрашивает allowlisted роль, scope и срок,
   другой принимает решение; active grant можно немедленно отозвать.

Инициатор и получатель не видят кнопки собственного независимого решения.
Интерфейс не смешивает временный grant с обычными ролями, показывает expiry и
понятные причины вместо внутренних кодов. Полученное временно право не даёт
создавать recovery или делегировать новое аварийное право.
## API admin

```text
GET/POST /admin/members
GET/PATCH /admin/members/{id}
POST /admin/members/{id}/verify
POST /admin/members/{id}/suspend
POST /admin/members/{id}/start-exit
POST /admin/members/{id}/merge-case
GET/POST /admin/cooperatives
POST /admin/memberships
POST /admin/role-assignments
POST /admin/role-assignments/{id}/revoke
GET /admin/accounts/{id}/sessions
POST /admin/accounts/{id}/revoke-sessions
GET/POST /api/v1/admin/account-recoveries
POST /api/v1/admin/account-recoveries/{id}/decision
GET/POST /api/v1/admin/break-glass
POST /api/v1/admin/break-glass/{id}/decision
POST /api/v1/admin/break-glass/{id}/revoke
GET /api/v1/auth/security
POST /api/v1/auth/totp/enrollment
POST /api/v1/auth/totp/enrollment/confirm
POST /api/v1/auth/step-up/totp
DELETE /api/v1/auth/totp
GET/POST /admin/service-clients
POST /admin/imports
POST /admin/imports/{id}/dry-run
POST /admin/imports/{id}/apply
GET /admin/audit
```

## UX защиты

- обязательная смена bootstrap-пароля не блокирует штатный выход из сессии;
- мобильная навигация удерживает активный раздел в видимой области и не показывает системный scrollbar;
- массовое действие всегда показывает count, filter snapshot и последствия;
- privileged change имеет preview и independent approval;
- PII export требует reason и журналируется;
- destructive-looking action объясняет, что произойдёт с obligations/history;
- administrator видит только свою organization/scope;
- impersonation запрещён; support использует controlled view-as с audit без
  права подписывать от имени участника.

## Acceptance

- можно завести человека, организацию, membership и user отдельно;
- duplicate check не выполняет silent merge;
- suspension не удаляет обязательства;
- role revoke немедленно блокирует новую критическую подпись;
- администратор организации не видит PII другой организации;
- один человек не выдаёт себе privileged role;
- все изменения воспроизводятся из administrative events/audit;
- external service/client может быть отозван без остановки локальных accounts.
## Эксплуатационная сводка

Для `COOPERATIVE_ADMIN`, `SECURITY_ADMIN` и `AUDITOR` доступен отдельный
read-only раздел: signed journal, outbox/quarantine, sessions, trust/appeals,
federation conflicts/incidents/key rotations/offline epochs/forms и crisis
state. Он не даёт обходить специализированные role workspaces и не содержит
универсального рейтинга человека.

## Реализованное рабочее место Slice 22

Раздел **Администрирование** открывает пять самостоятельных вкладок:
**Организации**, **Участники**, **Членства**, **Учётные записи** и **Узлы**.
Создание Member не создаёт login; создание User не создаёт membership и не
назначает роль. Узел показывает техническое состояние и ведёт в отдельный
контур federation trust, ответственности и сертификатов.

Registrar выбирает организацию явно. Scoped administrator не видит чужие
организации, участников и memberships даже при прямом API-вызове. Действия
приостановки и отключения меняют versioned status, требуют причину и сохраняют
историю. Кнопки удаления отсутствуют. Security administrator не может отключить
собственную текущую учётную запись; отключение другой записи сразу завершает её
активные сессии.
## Реализованный безопасный импорт Slice 23

Вкладка «Импорт» реализует полный операторский путь: шаблон CSV, выбор организации, staging, dry run, счётчики и построчный отчёт, независимое утверждение/отклонение и применение. Регистратор не может утвердить собственный пакет. Перед применением сервер повторяет проверку, а устаревший отчёт останавливает всю транзакцию.

Ручная форма участника сначала показывает duplicate candidates. Точный identifier блокирует регистрацию; одинаковое нормализованное имя требует явной отметки «это другой человек». Импорт не назначает доступ, роли, членства, паи или лимиты.
