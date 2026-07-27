# Руководство пользователя / User Guide

[Русский](#русский) | [English](#english) | [Главная / Home](../README.md)

## Русский

### Первый вход

1. Откройте адрес узла, обычно `http://localhost:8080` в локальной установке.
2. Введите логин и временный пароль, полученные у администратора.
3. Если система попросит, сразу задайте новый пароль.
4. Переключатель языка и кнопка светлой/темной темы всегда находятся в правом верхнем углу.

Меню показывает только разделы, доступные вашей роли. Отсутствие кнопки обычно означает, что действие должен выполнить другой ответственный человек.

Подключение второго фактора, подтверждение важных действий и восстановление при потере телефона описаны отдельно: [безопасность учётной записи](user-guide/account-security.md).

### Мой кабинет

После входа обычный пайщик попадает на **Главную**. Здесь собрана его собственная, а не общая статистика узла:

- имя, логин, кооператив, номер пайщика и дата вступления;
- всего паёв, доступная, защищённая и заблокированная части;
- уже исполненная выручка, ожидаемые поступления и сумма, которую должен исполнить сам пайщик;
- все прежние предложения, включая снятые и истёкшие;
- мои заказы и заказы других участников по моим предложениям;
- открытые обязательства, сроки и возможность взаимозачёта;
- поручительства и иное паевое обеспечение риска;
- основание каждого начисления, версия политики, её контрольный хеш и максимальная допустимая экспозиция.

Паевой капитал и оценка товара не смешиваются. Вклад появляется только по записи-основанию кооператива. Оценку товара или услуги задаёт поставщик; доставка и обязательные сборы показываются отдельно. Подтверждение обмена создаёт сделку и обязательства, но не списывает основной пай как внутреннюю валюту.

Кнопки **Предложить товар или услугу**, **Найти нужное** и **Мои сделки** ведут прямо к соответствующему действию.
### Получить молоко, гвозди или другой товар за паи

1. Откройте **Рынок** и вкладку **Товары**.
2. Нажмите популярный товар или введите название в поле **Товар**.
3. Укажите количество, единицу измерения и район или населённый пункт доставки. На этом шаге точный домашний адрес не публикуется.
4. Нажмите **Найти товары**.
5. Сравните карточки. Крупная сумма **Итого с доставкой** уже включает товар, логистику и обязательные сборы.
6. Проверьте продавца, доступный остаток, минимальную партию, срок доставки и отметку проверенного источника.
7. Нажмите **Получить за паи** на подходящей карточке.
8. В окне оформления укажите точный адрес доставки, имя и телефон получателя, а при необходимости инструкции для въезда или разгрузки. Эти сведения не попадают в общий каталог.
9. Откройте **Мои заказы**. Проверьте сохранённую точку доставки, последовательно зарезервируйте товар и доставку, затем нажмите **Подтвердить обмен**.
10. Откройте **Сделки**: завершённый обмен должен показывать товар, поставщика, итог в паях и статус **Обмен подтверждён**.

Если связь оборвалась, заказ не исчезает. Его статус остается в **Моих заказах**, и разрешенное действие можно повторить. Не создавайте второй заказ, пока не проверили первый.

### Предложить молоко, капусту или другой товар

Право `EXCHANGE_PARTICIPANT` позволяет пайщику искать нужное и публиковать собственные предложения. Публикацию подписывает сам участник, поэтому она связана с конкретным человеком и остаётся в истории после снятия.

1. Убедитесь, что товар действительно существует или что вы реально можете выполнить услугу в указанный срок. Для промышленной эксплуатации партия, хранение и качество дополнительно подтверждаются ответственными лицами.
2. На **Главной** нажмите **Предложить товар или услугу** либо откройте **Рынок** и вкладку **Предложить**.
3. Выберите **Товар** или **Услуга**. Укажите простое название, понятное другим людям.
4. Выберите единицу: килограммы, литры, штуки или часы.
5. Укажите доступное количество, минимальную партию, оценку за единицу, публичный район и срок действия.
6. Ниже укажите приватную точку забора: точный адрес, имя ответственного, телефон и инструкции для подъезда. Эти данные увидят только стороны подтверждённой сделки и назначенный перевозчик.
7. При необходимости выберите фотографию JPEG, PNG или WebP. До публикации показывается предварительный просмотр; файл сохраняется как отдельное проверяемое доказательство.
8. Проверьте форму и нажмите **Опубликовать предложение**.
9. Вернитесь на **Главную**: предложение появится в **Моих предложениях** вместе с адресом забора. Там его можно снять, не удаляя историю.

Если публикация недоступна, учётная запись не связана с активным пайщиком либо право участника рынка отозвано. Администратор проверяет это в разделе **Доступ**.

### Что означают статусы покупки

| Статус | Что происходит | Что делать |
|---|---|---|
| Оформление начато | создано намерение купить | зарезервировать товар |
| Товар зарезервирован | продавец удерживает нужное количество | зарезервировать доставку |
| Готово к подтверждению | товар и доставка удерживаются | проверить итог и подтвердить |
| Узлы подтверждают | идет межузловая фиксация | дождаться или безопасно повторить подтверждение |
| Резервы освобождаются | выполняется отмена | дождаться завершения |
| Обмен подтверждён | подписанные резервы зафиксированы | открыть **Сделки** и перейти к исполнению и приёмке |
| Отменено или истекло | сделка не состоялась | при необходимости начать заново |

### Сделка с отдельным логистом

Подробный реально пройденный пример со скриншотами: [доставка молока отдельным логистом](user-guide/logistics-provider-transaction.md).

Логист находит товар на рынке, публикует подписанный расчёт конкретного маршрута и заранее указывает стоимость, сроки, вместимость и предел ответственности. Покупатель видит доставку отдельно от товара, резервирует её и подтверждает общий обмен. После этого система автоматически создаёт рейс и назначает его подписавшему расчёт перевозчику. Логист отдельно подтверждает принятие, погрузку и доставку; для двух физических этапов требуются разные акты. После отметки логиста **Доставлено** продавец открывает **Сделки**, прикладывает фото или акт и нажимает **Товар передан**. Покупатель проверяет груз, указывает фактически принятое количество и состояние, прикладывает своё подтверждение и нажимает **Подтвердить получение**. Недостача, повреждение или отказ фиксируются явно и не превращаются в молчаливое полное исполнение.

### Проверочный путь «молоко на гвозди»

Подробный реально пройденный пример со скриншотами: [получить ремонт компьютера за молоко](user-guide/computer-repair-for-milk.md).

1. `registrar` регистрирует фермера, переводит его в статус **Активен** и оформляет членство.
2. `security` создаёт связанную с фермером учётную запись и выбирает профиль **Также предлагать свои товары**.
3. `auditor` независимо одобряет право публикации.
4. Фермер входит по временному паролю, меняет его и во вкладке **Предложить** публикует 100 литров молока.
5. Во вкладке **Товары** фермер выбирает **Гвозди**, указывает 100 штук и сравнивает полную стоимость с доставкой.
6. В **Моих заказах** фермер резервирует товар, затем доставку и подтверждает обмен.
7. В **Сделках** фермер видит завершённый заказ со статусом **Обмен подтверждён**.

Это обмен через паевый учёт, а не обязательная прямая связка с одним владельцем гвоздей. Публикация молока создаёт предложение; паи и встречные обязательства возникают только после того, как другой участник примет это предложение. Клиринг затем сворачивает совместимые обязательства, но не подменяет физическую поставку молока или гвоздей.

### Пользователи, роли и права

Учетными записями управляет пользователь с ролью `SECURITY_ADMIN` в разделе **Доступ**:

1. Под логином `registrar` откройте **Участники**, зарегистрируйте человека, активируйте его после проверки и создайте членство.
2. Под логином `security` откройте **Доступ**, выберите участника и задайте ему логин и временный пароль.
3. Выберите простой профиль: **Искать и получать товары за паи** либо **Также предлагать свои товары**.
4. Для продавца под логином `auditor` одобрите право публикации в блоке **Ожидают независимого решения**.
5. Передайте человеку логин и временный пароль. При первом входе он обязательно заменит пароль.
6. Для прекращения доступа отключите учетную запись или отзовите роль. История действий не удаляется.

Подробности: [административная консоль](admin_console.md).

### Проверка аномалий для риск-менеджера и аудитора

1. Под ролью `RISK_ADMIN` откройте **Проверка аномалий**, выберите кооператив и период, затем нажмите **Запустить проверку**.
2. Откройте сигнал и прочитайте отдельно **Что обнаружено** и **С каким порогом сравнили**. Сигнал не означает, что нарушение доказано.
3. Человек, запустивший проверку, передаёт сигнал другому сотруднику с ролью `AUDITOR`.
4. Аудитор нажимает **Взять на проверку**, проверяет первичные документы и загружает файл-доказательство.
5. Аудитор пишет понятное обоснование и выбирает **Снять удержание** либо **Подтвердить риск**. Решение без доказательства не сохраняется.

Пока сигнал `HOLD` открыт, связанная автоматическая операция недоступна. Не
обходите удержание новым аккаунтом или другим экраном: исправьте исходные данные
или передайте спор в установленную процедуру.
### Простые правила безопасности работы

- не передавайте пароль другому человеку;
- перед подтверждением читайте количество, срок, итоговую цену и свою ответственность;
- не подтверждайте получение до фактической приемки товара;
- прикладывайте доказательства качества и передачи там, где их требует форма;
- при расхождении остановите процесс и откройте спор, а не исправляйте старую запись вручную;
- при плохой связи сначала проверьте текущий статус, затем повторяйте действие.

### Личная адресная книга

1. На **Главной** найдите раздел **Мои адреса** и нажмите **Добавить адрес**.
2. Дайте точке короткое понятное имя: **Ферма**, **Дом**, **Склад** или своё.
3. Выберите назначение: забор, доставка или оба варианта. Укажите публичный район для расчёта маршрута, точный адрес, контакт, телефон и инструкции.
4. При необходимости сделайте точку основной для забора или доставки. Основная точка подставляется автоматически, но перед публикацией или заказом её можно заменить и проверить.
5. В форме предложения выберите сохранённую точку забора. В поиске товара сначала выберите точку доставки, чтобы логистика считалась для правильного района.
6. Изменение адресной книги не переписывает старые предложения и заказы: в каждом из них хранится собственная копия адреса. Удаление точки архивирует её и также не портит историю.

Точный адрес и телефон не публикуются в общем каталоге. После подтверждения обмена их видят только продавец, покупатель, назначенный перевозчик и явно уполномоченный контролёр.

Иллюстрированный пример по всем трём ролям: [адреса забора и доставки](user-guide/addresses-and-delivery.md).

## English

### First sign-in

1. Open the node address, usually `http://localhost:8080` for a local installation.
2. Enter the login and temporary password provided by an administrator.
3. Set a new password immediately when prompted.
4. Language and light/dark theme controls are always in the top-right corner.

The menu shows only sections available to your role. A missing button usually means that another responsible person must perform the action.

### My account

An ordinary member lands on **Home** after signing in. It shows the member's own position rather than node-wide statistics:

- name, login, cooperative, member number, and joining date;
- total, available, protected, and reserved shares;
- settled earnings, expected incoming value, and the amount the member still owes;
- all previous offers, including revoked and expired ones;
- my orders and orders placed against my offers;
- open obligations, due dates, and clearing eligibility;
- guarantees and other share-backed exposure;
- the source of each contribution, policy version, control hash, and maximum exposure.

Share capital and offer valuation are separate. A contribution exists only because of a cooperative source record. The supplier sets the goods or service valuation; logistics and mandatory fees remain visible. Confirming an exchange creates a deal and obligations, but it does not spend primary shares as an internal currency.

The **Offer goods or a service**, **Find what I need**, and **My deals** buttons go directly to those tasks.

### Personal address book

1. On **Home**, find **My places** and select **Add place**.
2. Give it a short name such as **Farm**, **Home**, or **Warehouse**.
3. Choose pickup, delivery, or both. Enter the public routing area, exact address, contact, phone, and access instructions.
4. Optionally make it the default pickup or delivery point. The default is filled automatically and can still be checked or replaced before confirmation.
5. Select a saved pickup point while publishing an offer. Select the delivery point before searching so the logistics quote uses the correct public area.
6. Editing or archiving a place never rewrites an existing offer or order. Each transaction stores its own address snapshot.

Exact addresses and phone numbers are not published in the shared catalog. After confirmation, only the supplier, recipient, assigned carrier, and explicitly authorized controller may see them.

See the illustrated three-role walkthrough: [pickup and delivery addresses](user-guide/addresses-and-delivery.md#english-summary).

### Receive milk, nails, or another product for shares

1. Open **Market** and the **Goods** tab.
2. Select a popular product or type its name under **Product**.
3. Enter the quantity, unit, and delivery area.
4. Select **Find products**.
5. Compare product cards. The large **Delivered total** already includes goods, logistics, and mandatory fees.
6. Check the seller, available stock, minimum order, delivery time, and verified source mark.
7. Select **Get for shares** on the appropriate card.
8. Open **My orders**. Reserve goods and delivery in sequence, then select **Confirm exchange**.
9. Open **Deals**: the completed exchange must show the product, supplier, total shares, and **Exchange confirmed** status.

If connectivity is interrupted, the order remains under **My orders** with its current status. Retry the allowed action there. Do not create a second order before checking the first one.

### Offer milk, cabbage, or another product

The `EXCHANGE_PARTICIPANT` permission lets a member search the market and publish their own offers. The member signs each publication personally, so it remains tied to a named person and stays in history after revocation.

1. Confirm that the goods exist or that you can perform the service by the stated deadline. In a real pilot, responsible people additionally verify lots, custody, and quality.
2. Select **Offer goods or a service** on **Home**, or open **Market** and the **Offer** tab.
3. Select **Goods** or **Service**, then enter a plain title others can understand.
4. Select kilograms, litres, pieces, or hours.
5. Enter available quantity, minimum order, valuation per unit, pickup area, and expiry.
6. Optionally select a JPEG, PNG, or WebP image. A preview appears before publication, and the file is stored as separate verifiable evidence.
7. Review the form and select **Publish offer**.
8. Return to **Home**. The offer appears under **My offers**, where it can be revoked without deleting its history.

If publishing is unavailable, the account is not linked to an active member or the market permission has been revoked. An administrator checks this under **Access**.

### Purchase statuses

| Status | Meaning | Action |
|---|---|---|
| Checkout started | a purchase intent exists | reserve goods |
| Goods reserved | the seller holds the required quantity | reserve delivery |
| Ready to confirm | goods and delivery are held | review the total and confirm |
| Nodes are confirming | inter-node commit is running | wait or safely retry confirmation |
| Releasing reservations | cancellation is running | wait for completion |
| Exchange confirmed | signed reservations are committed | open **Deals** and continue fulfillment and acceptance |
| Cancelled or expired | the transaction did not complete | start again when needed |

### Transaction with a separate carrier

See the illustrated walkthrough: [milk delivery by a separate logistics provider](user-guide/logistics-provider-transaction.md#english-summary).

The carrier finds goods in the market and publishes a signed quote for a specific route, including valuation, dates, capacity, and liability limit. The buyer sees logistics separately, reserves it, and confirms the combined exchange. The system then creates a route assigned to the carrier who signed the selected quote. Acceptance, pickup, and delivery are recorded separately, with distinct evidence for the two physical handovers. After the carrier reports delivery, the supplier records **Goods handed over** with evidence. The recipient inspects the load, records the quantity actually received and its condition, adds receipt evidence, and selects **Confirm receipt**. A shortage, damage, or rejection is stored explicitly instead of silently closing the full obligation.

### Verification path: milk for nails

1. `registrar` registers the farmer, moves them to **Active**, and creates membership.
2. `security` creates an account linked to the farmer and selects **Also offer own goods**.
3. `auditor` independently approves publishing permission.
4. The farmer signs in with the temporary password, changes it, and publishes 100 litres of milk under **Offer**.
5. Under **Goods**, the farmer selects **Nails**, enters 100 pieces, and compares delivered totals.
6. Under **My orders**, the farmer reserves goods, reserves delivery, and confirms the exchange.
7. Under **Deals**, the farmer sees the completed order with **Exchange confirmed** status.

This is an exchange through share accounting, not a mandatory direct swap with one nail supplier. Publishing milk creates an offer; shares and reciprocal obligations arise only after another participant accepts it. Clearing later nets compatible obligations but never replaces physical delivery of milk or nails.

### Users, roles, and permissions

An account with the `SECURITY_ADMIN` role manages access under **Access**:

1. As `registrar`, open **Members**, register the person, activate them after verification, and create membership.
2. As `security`, open **Access**, select the member, and set a login and temporary password.
3. Choose a simple profile: **Find and receive goods for shares** or **Also offer own goods**.
4. For a seller, sign in as `auditor` and approve publishing under **Awaiting independent decision**.
5. Give the person their login and temporary password. They must replace the password at first sign-in.
6. To stop access, disable the account or revoke its role. Action history is never deleted.

See the [administrative console guide](admin_console.md) for details.

### Anomaly review for the risk manager and auditor

1. As `RISK_ADMIN`, open **Anomaly review**, select the cooperative and period, then select **Run review**.
2. Open a signal and read **What was observed** separately from **Compared threshold**. A signal is not proof of wrongdoing.
3. The person who ran the scan hands the signal to another person with the `AUDITOR` role.
4. The auditor selects **Start review**, checks primary records, and uploads an evidence file.
5. The auditor writes a clear rationale and selects **Release hold** or **Confirm risk**. A decision without evidence is rejected.

While a `HOLD` signal is active, its related automatic operation remains
unavailable. Do not bypass the hold through another account or screen; correct
the source facts or use the established dispute procedure.
### Simple operating rules

- never share a password;
- read the quantity, deadline, delivered total, and responsibility before confirming;
- do not confirm receipt before physically accepting the goods;
- attach quality and transfer evidence whenever the form requires it;
- stop and open a dispute when facts differ instead of editing old history;
- after connectivity trouble, check the current status before retrying an action.
## Подключение внешней программы

Этот раздел предназначен для администратора кооператива, а не для обычного
пайщика. Не создавайте для программы учётную запись человека.

1. Войдите пользователем с постоянной ролью `COOPERATIVE_ADMIN` или `SECURITY_ADMIN`.
2. Откройте **Реестры системы -> Интеграции**.
3. Укажите кооператив-владелец, название, ответственного специалиста и его email.
4. Отметьте только реально нужные действия. Для обычного поиска оставьте только **Поиск товаров и услуг**.
5. Укажите конкретный IP или сеть внешней программы, лимит и срок, затем нажмите **Отправить на проверку**.
6. Другой администратор безопасности входит своей учётной записью, открывает ту же вкладку, выбирает **Одобрить** и подтверждает действие шестизначным кодом.
7. Сразу сохраните показанные идентификатор и секрет в secret store внешней программы. После закрытия окна секрет восстановить нельзя.

При подозрении на утечку администратор безопасности использует **Выпустить новый
секрет**. Для немедленной остановки выбирается **Приостановить**, а для
окончательного отключения - **Навсегда отозвать доступ**. Эти действия не
отключают вход сотрудников. Технические и security-инварианты описаны в
[реализованном Slice 24](implemented_slice_24.md).