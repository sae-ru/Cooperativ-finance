# Экономика без денег

### Economy Without Money

**Cooperative Clearing**

[Русский](#русский) | [English](#english)

## Русский

**Cooperative Clearing** - открытая local-first система для обмена товарами и услугами между кооперативами, фермерами, мастерскими, перевозчиками и местными сообществами. Она помогает продолжать хозяйственный обмен, даже когда обычные платежные сети, банки или связь работают нестабильно.

Человек видит простой рынок: выбирает молоко, гвозди, капусту или другой товар, указывает количество и место доставки, а система ищет предложения на доступных узлах. Цена показывается сразу вместе с логистикой и обязательными сборами. После подтверждения товар и доставка резервируются, а действия участников попадают в подписанный журнал.

### Что дает система

- локальную работу узла без постоянного Интернета;
- поиск товаров и доставки по федерации независимых серверов;
- детерминированный клиринг встречных обязательств;
- понятную цепочку персональной ответственности на каждом этапе;
- ограниченную ответственность паями только по заранее принятому риску;
- отдельный контур добровольной помощи без долга и покупки репутации;
- роли, права, независимые проверки, споры и апелляции;
- объяснимые сигналы аномалий с временным удержанием и независимым решением;
- подписанные события, доказательства и восстановление после разрыва связи;
- русский и английский интерфейс, светлую и темную темы;
- Docker-развертывание на Linux и лицензию MIT.

### Запуск одной командой

Нужны только Docker Desktop на Windows либо Docker Engine с Compose на Linux.

```bat
start.bat
```

```bash
sh ./start.sh
```

Обе команды создают конфигурацию и секреты, собирают контейнеры, применяют миграции, загружают демоданные и проверяют готовность узла. После запуска откройте `http://127.0.0.1:8080`. Демо-логины и пароли приведены в [руководстве по развёртыванию](docs/deployment.md#демонстрационные-учетные-записи).

Для узла без демонстрационных данных используйте `start.bat production` или `sh ./start.sh production`; в этом режиме пароли генерируются случайно.
### Простой путь покупки

1. Откройте **Рынок**.
2. Выберите популярный товар или введите название.
3. Укажите количество и место доставки.
4. Сравните карточки по полной цене с доставкой.
5. Нажмите **Получить за паи** и подтвердите резервирование в разделе **Мои заказы**.

### Документация

- [Концепция и границы системы](docs/concept.md)
- [Руководство пользователя](docs/user_guide.md)
- [Установка и эксплуатация](docs/deployment.md)
- [Архитектура](docs/architecture.md)
- [Архитектура интерфейса](docs/gui_architecture.md)
- [Алгоритм клиринга](docs/clearing_algorithm.md)
- [Федеративный поиск каталога](docs/federated_catalog_search.md)
- [Администрирование пользователей, ролей и прав](docs/admin_console.md)
- [Как добавить язык интерфейса](lang/README.md)
- [Полный индекс документации](docs/README.md)

> Система является инженерной основой для пилотов. Экономические лимиты, правила ответственности и юридически значимые процедуры должны быть утверждены участниками и проверены для конкретной юрисдикции до реального использования.

## English

**Cooperative Clearing** is an open local-first system for exchanging goods and services among cooperatives, farmers, workshops, carriers, and local communities. It is designed to keep practical trade moving when normal payment networks, banks, or connectivity become unreliable.

A person sees a simple marketplace: choose milk, nails, cabbage, or another product, enter the quantity and delivery area, and let the system search reachable nodes. The delivered price includes logistics and mandatory fees. After confirmation, goods and delivery are reserved, while participant actions are recorded in a signed journal.

### What the system provides

- local node operation without permanent Internet access;
- product and delivery search across a federation of independent servers;
- deterministic clearing of mutual obligations;
- an explicit chain of personal responsibility at every stage;
- share-backed liability limited to risks accepted in advance;
- a separate voluntary aid circuit that creates neither debt nor reputation;
- roles, permissions, independent checks, disputes, and appeals;
- explainable anomaly signals with temporary holds and independent decisions;
- signed events, evidence, and recovery after connectivity loss;
- Russian and English UI with light and dark themes;
- Linux Docker deployment under the MIT license.

### One-command startup

The only prerequisite is Docker Desktop on Windows or Docker Engine with Compose on Linux.

```bat
start.bat
```

```bash
sh ./start.sh
```

Both commands create configuration and secrets, build containers, apply migrations, load demo data, and verify node readiness. Then open `http://127.0.0.1:8080`. Demo credentials are listed in the [deployment guide](docs/deployment.md#demo-accounts).

For a node without demo data, use `start.bat production` or `sh ./start.sh production`; this mode generates random passwords.
### Simple buying flow

1. Open **Market**.
2. Choose a popular product or type its name.
3. Enter the quantity and delivery area.
4. Compare product cards by the full delivered price.
5. Select **Get for shares** and confirm reservations under **My orders**.

### Documentation

- [System concept and boundaries](docs/concept.md)
- [User guide](docs/user_guide.md)
- [Installation and operations](docs/deployment.md)
- [Architecture](docs/architecture.md)
- [Interface architecture](docs/gui_architecture.md)
- [Clearing algorithm](docs/clearing_algorithm.md)
- [Federated catalog search](docs/federated_catalog_search.md)
- [User, role, and permission administration](docs/admin_console.md)
- [Adding an interface language](lang/README.md)
- [Complete documentation index](docs/README.md)

> The system is an engineering foundation for pilots. Economic limits, liability rules, and legally significant procedures must be approved by participants and reviewed for the applicable jurisdiction before real-world use.

## License

Source code is distributed under the [MIT License](LICENSE).