# Реализованный Slice 39: платформенный контракт офлайн-релиза

Статус: code-level acceptance criterion 122 реализован fail-closed.

Release bundle v2 подписывает не только архитектуру каждого образа, но и общий
платформенный контракт релиза. Для текущего релиза должна быть квалифицирована
ровно одна платформа: `linux/amd64` либо `linux/arm64`. Вторая архитектура
явно записывается как `not-qualified-for-this-release`.

## Инварианты

- сборщик требует `--qualified-platform`;
- backend, frontend, gateway и PostgreSQL обязаны иметь одинаковые OS/arch;
- все четыре образа обязаны совпасть с квалифицированной платформой;
- `amd64` и `arm64` должны быть либо квалифицированы, либо явно исключены;
- независимый verifier принимает `--expected-platform`;
- при `--load-images` verifier сверяет platform Docker host до импорта;
- после импорта повторно проверяются content ID и platform каждого образа;
- bundle v1 не выдаётся за новый production-контракт v2.

Квалификация означает, что именно на этой платформе выполнен полный release
gate. Наличие multi-arch base image или успешная сборка без приёмочных тестов
квалификацией не считается.

## Проверки

- 17 положительных и отрицательных тестов release bundle;
- полный набор operator scripts: `49 passed`;
- корректный `linux/amd64` bundle принимается;
- корректный fixture `linux/arm64` принимается тем же verifier;
- missing contract, mixed images, missing ARM64 exclusion, expected mismatch и
  wrong Docker host отклоняются до импорта;
- Python compile, Bash syntax и PowerShell parser зелёные.

Реальный локальный gate собрал bundle `slice39-platform-contract` из текущих
backend/frontend/gateway/PostgreSQL images. Независимый verifier подтвердил
подпись, отдельно вычисленный policy SHA-256, `linux/amd64`, 4 content IDs,
45 node payload files и повторный `docker load`. License inventory:
`allowed=107`, `blocked=0`, `review_required=161`; последний класс требует
ручного решения и не считается автоматически одобренным. Public fingerprint:
`sha256:df1eb28757d1106b98aba601100fc5195f5986a740a3f50468d65fa7170c549a`.
Компактное evidence без private key и OCI archives:
`evidence/platform-release-20260728T175332Z`.

## Границы

Этот срез доказывает исполняемый формат и симметрию verifier. Фактический ARM64
release считается поддерживаемым только после запуска полного CI/acceptance на
ARM64 runner и подписи соответствующего bundle. До этого каждый AMD64 release
явно исключает ARM64.

Production key ceremony, независимый license/security review и публикация
конкретного подписанного release остаются отдельными production gates.
