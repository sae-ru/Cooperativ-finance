# Реализованный Slice 28: fail-closed production deployment

Статус: реализован code-level эксплуатационный барьер. Он не заменяет
подписанный production readiness review, внешнюю проверку безопасности,
юридические решения или полевое учение.

## Проблема

Приложение использует каноническое значение `COOP_ENVIRONMENT=production`, но
часть эксплуатационных скриптов исторически проверяла сокращение `prod`.
Кроме того, прежний `start.* production` отключал demo seed, но оставлял
runtime environment равным `dev` из `.env` и собирал образы из локального
исходного кода. Такой запуск выглядел как production, не выполняя production
ограничения.

## Реализованный контракт

- единственные допустимые значения окружения:
  `dev`, `test`, `staging-node`, `pilot`, `production`;
- `scripts/runtime_environment.py` одинаково разрешает environment из процесса
  и `.env`, отклоняя неканонический alias;
- `start.bat` и `start.sh` сохраняют выбранный режим в `.env`, а не только в
  окружении текущего процесса;
- абсолютные пути проверенного release bundle/public key и утверждённый policy hash
  сохраняются как не-секретные operational settings для backup/update;
- demo-режим всегда использует `dev`, demo profile и известные учебные данные;
- production-режим требует подписанный offline bundle, независимый public key,
  ожидаемый release id и утверждённый SHA-256 license policy;
- verifier проверяет bundle и загружает образы до старта;
- production Compose запускается только с `--no-build --pull never`;
- production bootstrap создаёт случайные начальные пароли;
- существующий `.env` с demo data и известные demo bootstrap credentials
  запрещают in-place promotion;
- профиль узла в PostgreSQL независимо запрещает hardened startup при
  `demo_data_loaded=true` и смену между hardened/non-hardened окружениями;
- update scripts требуют signed bundle, запрещают build/faultpoints и требуют
  FULL backup именно для значения `production`;
- production evidence всегда требует чистый Git worktree; опасный override
  запрещён.

## Запуск

Учебный узел:

```bash
sh ./start.sh
```

Production Linux:

```bash
sh ./start.sh production \
  /media/cooperative-clearing-1.0.0 \
  /etc/cooperative-clearing/release-public.pem \
  1.0.0 \
  <approved-license-policy-sha256>
```

Windows использует те же четыре параметра после `start.bat production`.
Запуск production поверх демонстрационного узла намеренно отклоняется. Нужна
чистая установка; переносить разрешено только отдельно проверенные
production backup/evidence.

## Проверка

- unit tests контракта environment: precedence, canonical values, atomic `.env`,
  fresh production, запрет demo promotion, demo credentials и downgrade;
- static contract tests обеих пар start/update/evidence scripts;
- PowerShell parser и Linux `sh -n`;
- PostgreSQL integration: demo marker, in-place transition и idempotent
  production restart;
- release bundle tests автоматически включают новый helper в подписанный
  `node/` payload;
- живой demo-стенд после изменения обязан остаться `dev`, загрузить demo и
  пройти `verify-stack`.

Checkpoint: `244 passed, 1 deselected`, backend coverage `77.81%`, Ruff clean, strict mypy `258` files, `29` host-script tests, PowerShell parser и Linux `sh -n` clean, `alembic check` clean, OpenAPI `360` operations compatible и mirrors byte-identical. `start.bat demo` пересобрал живой узел; API подтвердил `OPERATIONAL`, `dev`, `demo_data_loaded=true`, worker `RUNNING`, schema `0034_custody_continuity`.

## Остаточные границы

Код не может сам подписать юридическое, security или governance решение.
Production разрешается организационно только после закрытия
`production_readiness.md`. Public key fingerprint, license policy hash,
release id, назначенные custodians и внешний readiness protocol должны быть
получены независимо от устанавливаемого bundle.
