# Безопасность учётной записи / Account security

[Русский](#русский) | [English](#english) | [Руководство / Guide](../user_guide.md)

## Русский

### Подключить приложение с кодами

1. Войдите под своей учётной записью и откройте **Безопасность**.
2. В блоке **Подключить второй фактор** введите текущий пароль и нажмите
   **Начать подключение**.
3. Откройте на телефоне любое совместимое приложение TOTP, выберите добавление
   учётной записи и отсканируйте показанный QR-код. Если камера недоступна,
   введите показанный ключ вручную.
4. Введите новый шестизначный код и нажмите **Подтвердить**. Один код нельзя
   использовать дважды.
5. Закройте экран с QR. Система больше не показывает seed; для замены фактора
   потребуется текущий пароль и код старого приложения.

Не фотографируйте QR и не пересылайте ключ. Бумажную резервную запись храните
только по правилам кооператива отдельно от пароля.

### Подтвердить важное действие

Если система сообщает, что нужно подтвердить личность:

1. Откройте **Безопасность**.
2. Введите текущий код из приложения.
3. Нажмите **Подтвердить на 10 минут**.
4. Вернитесь к нужной операции и повторите её один раз.

Подтверждение относится только к текущей server-side сессии и автоматически
истекает. Финализация клиринга не обходится аварийной ролью.

### Потерян телефон или пароль

Не создавайте новую учётную запись и не просите администратора менять данные
напрямую в БД.

1. Обратитесь к ответственному сотруднику и пройдите локальную проверку личности.
2. Первый сотрудник создаст запрос recovery с номером акта и временным паролем.
3. Другой независимый сотрудник проверит акт и одобрит запрос.
4. После одобрения все прежние сеансы и TOTP будут отозваны.
5. Войдите временным паролем, сразу замените его и подключите новый TOTP.

### Временное аварийное право для сотрудников

В **Безопасности** уполномоченный сотрудник выбирает человека, только
разрешённую аварийную роль, кооператив или scope узла, срок 15-60 минут, причину
и номер инцидента. Другой сотрудник независимо одобряет запрос. Временное право
показывает срок, не становится обычной ролью, журналирует каждое использование
и немедленно исчезает после отзыва или истечения.

## English

### Connect an authenticator app

1. Sign in with your own account and open **Security**.
2. Under **Connect a second factor**, enter your current password and select
   **Start setup**.
3. Open any compatible TOTP authenticator app on your phone and scan the QR
   code. If the camera is unavailable, enter the displayed setup key manually.
4. Enter a new six-digit code and select **Confirm**. A code cannot be reused.
5. Leave the QR screen. The seed is not shown again; rotating the factor
   requires the current password and a code from the old app.

Do not photograph or send the QR/setup key. Store any approved paper recovery
record separately from the password.

### Confirm an important action

When the system asks for identity confirmation:

1. Open **Security**.
2. Enter the current code from the authenticator app.
3. Select **Confirm for 10 minutes**.
4. Return to the operation and repeat it once.

The confirmation belongs only to the current server-side session and expires
automatically. Clearing finalization cannot bypass it with an emergency role.

### Lost phone or password

Do not create a replacement account or ask an administrator to edit the
database directly.

1. Contact the responsible staff member and complete local identity checking.
2. The first staff member creates a recovery request with a record number and
   a temporary password.
3. A different authorized staff member independently checks and approves it.
4. Approval revokes every old session and TOTP factor.
5. Sign in with the temporary password, change it immediately, and connect a
   new TOTP factor.

### Temporary emergency permission for staff

In **Security**, an authorized staff member chooses a person, an allowlisted
emergency role, cooperative or node scope, a 15-60 minute duration, reason, and
incident record. Another staff member independently approves it. The temporary
permission shows its expiry, never becomes an ordinary role, audits every use,
and disappears immediately after revocation or expiry.
