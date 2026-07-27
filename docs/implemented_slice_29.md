# Slice 29. Локальная готовность узла и безопасная диагностика

Статус: реализован инженерный baseline. Он не заменяет проверку на целевом
оборудовании, назначение дежурных и подписанное решение о production readiness.

## Что реализовано

- защищённый `GET /api/v1/operations/host-readiness` для администратора,
  администратора безопасности и аудитора;
- пять локальных проверок: свободное место, расхождение часов приложения и БД,
  свежесть полной резервной копии, срок сертификатов и состояние ИБП;
- фоновый host probe без Интернета, который `start.bat` и `start.sh` запускают
  идемпотентно раз в минуту; процесс можно безопасно остановить только после
  сверки PID и свежего `monitor_id`;
- регистрация успешной полной резервной копии в ограниченном marker-файле без
  путей, персональных данных и содержимого копии;
- Prometheus-метрики готовности с фиксированными именами проверок и без PII;
- RU/EN экран **Эксплуатация** с понятными действиями вместо внутренних кодов;
- явная локализация всей эксплуатационной сводки через XML-ключи без смешения
  русского и английского текста;
- план содержимого и скачивание диагностического пакета из GUI;
- обязательное шифрование пакета `AES-256-GCM`, ключ из passphrase через
  `scrypt`, случайные salt и nonce;
- персональная append-only запись `DIAGNOSTIC_BUNDLE_EXPORTED` с request ID,
  размером и SHA-256 зашифрованного файла, но без passphrase и содержимого;
- автономная утилита `scripts/diagnostic_bundle.py`, которая принимает пароль
  только из файла, проверяет шифрование, точный ZIP inventory, размеры и SHA-256;
- PWA-восстановление после замены frontend image: устаревший lazy chunk вызывает
  не более одной автоматической перезагрузки за минуту, service worker обновляется,
  старые cache entries удаляются, а повторная ошибка показывает локализованный экран.

## Состав диагностики

В пакет входят только четыре файла:

1. `manifest.json`;
2. `operations.json`;
3. `host-readiness.json`;
4. `metrics.prom`.

Не включаются raw logs, персональные данные, секреты, токены, закрытые ключи и
полные подписанные payload. Имена файлов фиксированы; дубликаты, лишние записи,
zip-slip пути, слишком большие записи и повреждённые хэши отклоняются.

Расшифровка выполняется так:

```sh
python scripts/diagnostic_bundle.py \
  --input cooperative-clearing-diagnostic-YYYYMMDDTHHMMSSZ.ccdiag \
  --output-dir ./diagnostic-decoded \
  --passphrase-file ./diagnostic-passphrase.txt
```

Passphrase содержит от 16 до 128 символов и не передаётся аргументом командной
строки. Каталог назначения должен отсутствовать или быть пустым.

## Host probe

Обычный запуск узла автоматически выполняет:

```sh
python scripts/operational_status.py start-probe --root .
```

Повторная команда использует уже работающий монитор, если PID, `monitor_id` и
свежий probe согласованы. Для обслуживания:

```sh
python scripts/operational_status.py stop-probe --root .
```

На Linux проверка часов использует `timedatectl`, а ИБП при настройке
`COOP_UPS_NAME` читается через `upsc`. На Windows проверяется служба времени.
Для аппаратной интеграции допустимы явные ограниченные значения
`COOP_HOST_CLOCK_STATUS` и `COOP_UPS_STATUS`. Отсутствующий ИБП или неизвестный
сигнал в hardened-среде дают предупреждение и требуют решения оператора.

## Пороговые значения

Значения задаются в `.env` и валидируются fail-closed:

- диск: предупреждение 15%, критично 5% свободного места;
- часы: предупреждение 5 секунд, критично 60 секунд;
- полная копия: предупреждение 36 часов, критично 72 часа;
- сертификат: предупреждение 30 дней, критично 7 дней;
- stale host probe: 180 секунд.

Критический порог не может быть мягче предупреждающего.

## Проверки

- unit: вычисление статусов, stale probe, криптографический round-trip,
  повреждение ciphertext, неверный пароль;
- scripts: bounded marker-файлы, idempotent monitor, подтверждённая остановка,
  backup marker, точный ZIP inventory, duplicate и oversized rejection;
- PostgreSQL integration: RBAC, readiness API, encrypted download, метрики и
  персональная audit-запись без passphrase;
- frontend: API body/blob, понятные RU-статусы без `BACKUP_DATA_ONLY` и
  `NOT_CONFIGURED`, заблокированная кнопка до совпадения passphrase, полная
  английская страница без кириллицы и PWA recovery boundary;
- OpenAPI: 363 операции, совместимость с baseline и точное равенство frontend.

Контрольный прогон: Ruff, strict mypy по 262 Python-файлам, `249 passed, 1 deselected`, line coverage `77.29%`; 41 script tests; 66 frontend test files и
186 tests с coverage `81.82%` statements / `70.77%` branches / `75.08%`
functions / `87.48%` lines; production PWA build; 21 critical PostgreSQL tests,
цикл миграции `0033 -> 0034 -> 0033 -> 0034`, signed journal verification и
трёхузловой acceptance `1 passed`. В браузере проверены RU/EN, light/dark,
desktop/mobile 390x844 без горизонтального overflow, понятная ошибка passphrase
и обновление ранее открытой PWA-вкладки после замены контейнера.

Исправлены исполняемые CI-контракты: frontend Dockerfile теперь собирается из
корневого context с доступом к `/lang`, migration gate использует актуальные
revision и secret filename, а readiness PostgreSQL проверяется по TCP внутри
Compose network. Локальные эквиваленты jobs зелёные; remote CI на конкретном
commit остаётся неподтверждённым до публикации изменений.

## Остаточные границы

Этот срез не закрывает проверку ИБП, дисков, часов и сертификатов на конкретном
production-сервере; не доказывает фактическое восстановление в RTO/RPO; не
назначает дежурных и не заменяет внешнюю security/privacy экспертизу. Эти пункты
остаются открытыми в `production_readiness.md` до появления подписанного
target-host evidence.
