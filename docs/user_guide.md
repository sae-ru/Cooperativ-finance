# Руководство пользователя / User Guide

[Русский](#русский) | [English](#english) | [Главная / Home](../README.md)

## Русский

### Первый вход

1. Откройте адрес узла, обычно `http://localhost:8080` в локальной установке.
2. Введите логин и временный пароль, полученные у администратора.
3. Если система попросит, сразу задайте новый пароль.
4. Переключатель языка и кнопка светлой/темной темы находятся в левой панели. На экране входа они находятся в правом верхнем углу.

Меню показывает только разделы, доступные вашей роли. Отсутствие кнопки обычно означает, что действие должен выполнить другой ответственный человек.

### Купить молоко, гвозди или другой товар

1. Откройте **Рынок** и вкладку **Купить**.
2. Нажмите популярный товар или введите название в поле **Товар**.
3. Укажите количество, единицу измерения и район доставки.
4. Нажмите **Найти товары**.
5. Сравните карточки. Крупная сумма **Итого с доставкой** уже включает товар, логистику и обязательные сборы.
6. Проверьте продавца, доступный остаток, минимальную партию, срок доставки и отметку проверенного источника.
7. Нажмите **Купить** на подходящей карточке.
8. Откройте **Мои заказы**. Последовательно зарезервируйте товар и доставку, затем нажмите **Подтвердить покупку**.

Если связь оборвалась, заказ не исчезает. Его статус остается в **Моих заказах**, и разрешенное действие можно повторить. Не создавайте второй заказ, пока не проверили первый.

### Продать молоко, капусту или другой товар

Публикацию подписывает человек с ролью `NODE_BUSINESS_OPERATOR`, потому что он отвечает за правдивость каталога. Фермер может работать под этой ролью, если она назначена и одобрена согласно правилам узла.

1. Сначала убедитесь, что товар реально существует, а складской остаток и ответственное хранение зарегистрированы в разделе **Склад**.
2. Откройте **Рынок** и вкладку **Продать**.
3. Выберите товар. Его фотография, единица измерения и типовая минимальная партия подставятся автоматически.
4. Укажите понятное описание, доступное количество, минимальную партию, цену за единицу, район отгрузки и срок действия.
5. Проверьте цифры и нажмите **Опубликовать предложение**.
6. После сообщения об успехе нажмите **Посмотреть на рынке**.

Если кнопка публикации недоступна, попросите администратора назначить ответственного оператора каталога. Система намеренно не разрешает анонимную публикацию непроверенного товара.

### Что означают статусы покупки

| Статус | Что происходит | Что делать |
|---|---|---|
| Оформление начато | создано намерение купить | зарезервировать товар |
| Товар зарезервирован | продавец удерживает нужное количество | зарезервировать доставку |
| Готово к подтверждению | товар и доставка удерживаются | проверить итог и подтвердить |
| Узлы подтверждают | идет межузловая фиксация | дождаться или безопасно повторить подтверждение |
| Резервы освобождаются | выполняется отмена | дождаться завершения |
| Покупка подтверждена | обязательства зафиксированы | перейти к исполнению и приемке |
| Отменено или истекло | сделка не состоялась | при необходимости начать заново |

### Пользователи, роли и права

Учетными записями управляет пользователь с ролью `SECURITY_ADMIN` в разделе **Доступ**:

1. Откройте **Участники**, чтобы зарегистрировать человека и членство в кооперативе.
2. Откройте **Доступ**, создайте логин и временный пароль.
3. Выберите пользователя и назначьте роль.
4. Привилегированную роль должен одобрить независимый уполномоченный, если это требует политика.
5. Для прекращения доступа отключите учетную запись или отзовите роль. История действий не удаляется.

Подробности: [административная консоль](admin_console.md).

### Простые правила безопасности работы

- не передавайте пароль другому человеку;
- перед подтверждением читайте количество, срок, итоговую цену и свою ответственность;
- не подтверждайте получение до фактической приемки товара;
- прикладывайте доказательства качества и передачи там, где их требует форма;
- при расхождении остановите процесс и откройте спор, а не исправляйте старую запись вручную;
- при плохой связи сначала проверьте текущий статус, затем повторяйте действие.

## English

### First sign-in

1. Open the node address, usually `http://localhost:8080` for a local installation.
2. Enter the login and temporary password provided by an administrator.
3. Set a new password immediately when prompted.
4. Language and light/dark theme controls are in the left sidebar. On the sign-in screen they appear in the top-right corner.

The menu shows only sections available to your role. A missing button usually means that another responsible person must perform the action.

### Buy milk, nails, or another product

1. Open **Market** and the **Buy** tab.
2. Select a popular product or type its name under **Product**.
3. Enter the quantity, unit, and delivery area.
4. Select **Find products**.
5. Compare product cards. The large **Delivered total** already includes goods, logistics, and mandatory fees.
6. Check the seller, available stock, minimum order, delivery time, and verified source mark.
7. Select **Buy** on the appropriate card.
8. Open **My orders**. Reserve goods and delivery in sequence, then select **Confirm purchase**.

If connectivity is interrupted, the order remains under **My orders** with its current status. Retry the allowed action there. Do not create a second order before checking the first one.

### Sell milk, cabbage, or another product

A person with the `NODE_BUSINESS_OPERATOR` role signs the publication and accepts responsibility for catalog accuracy. A farmer may hold this role after it is assigned and approved under node policy.

1. Confirm that the goods physically exist and that stock and custody are registered under **Inventory**.
2. Open **Market** and the **Sell** tab.
3. Choose the product. Its picture, unit, and typical minimum order are filled automatically.
4. Enter a clear description, available quantity, minimum order, unit price, pickup area, and expiry date.
5. Review the numbers and select **Publish offer**.
6. After the success message, select **View in market**.

When publishing is disabled, ask an administrator to assign a responsible catalog operator. The system intentionally prevents anonymous publication of unverified goods.

### Purchase statuses

| Status | Meaning | Action |
|---|---|---|
| Checkout started | a purchase intent exists | reserve goods |
| Goods reserved | the seller holds the required quantity | reserve delivery |
| Ready to confirm | goods and delivery are held | review the total and confirm |
| Nodes are confirming | inter-node commit is running | wait or safely retry confirmation |
| Releasing reservations | cancellation is running | wait for completion |
| Purchase confirmed | obligations are recorded | continue fulfillment and acceptance |
| Cancelled or expired | the transaction did not complete | start again when needed |

### Users, roles, and permissions

An account with the `SECURITY_ADMIN` role manages access under **Access**:

1. Open **Members** to register the person and cooperative membership.
2. Open **Access**, then create a login and temporary password.
3. Select the user and assign a role.
4. A privileged role requires independent approval when policy demands it.
5. To stop access, disable the account or revoke its role. Action history is never deleted.

See the [administrative console guide](admin_console.md) for details.

### Simple operating rules

- never share a password;
- read the quantity, deadline, delivered total, and responsibility before confirming;
- do not confirm receipt before physically accepting the goods;
- attach quality and transfer evidence whenever the form requires it;
- stop and open a dispute when facts differ instead of editing old history;
- after connectivity trouble, check the current status before retrying an action.