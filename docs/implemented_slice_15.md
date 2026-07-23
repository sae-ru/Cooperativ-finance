# Реализованный Slice 15: подписанный офлайн-релиз

Дата проверки: 2026-07-22.

Статус: инженерный Slice 15 реализован и локально проверен на полном комплекте
runtime-образов. Это не production-релиз: использован одноразовый тестовый ключ
и dirty development commit, а 160 системных лицензий требуют ручного review.

## Реализовано

- автономный producer/verifier `scripts/release_bundle.py`;
- Ed25519 key generation, подпись точных байтов manifest и fingerprint ключа;
- полный checksum inventory с запретом лишних файлов, симлинков и path traversal;
- экспорт четырёх runtime-образов и проверка их immutable content ID;
- CycloneDX 1.6 SBOM и license report для каждого образа;
- подписанная license policy и необязательная независимая pin-проверка её hash;
- отдельный installation payload без исходников и закрытых ключей;
- Linux и PowerShell wrappers для build/verify;
- `update-node.sh` и `update-node.ps1` используют один fail-closed verifier;
- CI contract создаёт и повторно проверяет signed offline bundle.

PowerShell update ранее проверял подпись, но не выполнял checksum inventory.
Теперь обе платформы проходят один и тот же Python verifier до `docker load`.

## Модель отказа

До загрузки первого образа verifier проверяет все 38 файлов. Отказ вызывают:

- неверная подпись или public-key fingerprint;
- другой expected release либо независимо утверждённый policy hash;
- отсутствующий, лишний, изменённый или небезопасный filesystem entry;
- неполные роли backend/frontend/gateway/PostgreSQL;
- несовпадение archive, SBOM, license report или image ID;
- неверно посчитанная классификация лицензии;
- любая лицензия со статусом `blocked`.

`review_required` остаётся явным residual risk и не превращается в
автоматическое одобрение.

## Автоматические проверки

```text
python -m unittest discover -s scripts/tests -p "test_*.py"
9 tests, OK

Ruff
All checks passed

python -m py_compile
PASS

bash -n build/verify/update wrappers
PASS

PowerShell parser build/verify/update wrappers
PASS
```

Негативные тесты покрывают altered archive, extra file, unsafe checksum path,
wrong key, wrong release, wrong policy digest, blocked license и ложную
классификацию лицензии.

## Живая приёмка

Одноразовый bundle `0.1.0-dev` собран из реально запущенных образов:

- 38 файлов, 557 830 714 байт;
- 4 runtime image archives;
- 22 installation payload files;
- schema revision `0018_inter_node_clearing`;
- peer `CC-PEER-1`, sync `1.0`, clearing `1.0.0`;
- 78 автоматически разрешённых package licenses;
- 0 запрещённых;
- 160 `review_required`;
- pinned policy SHA-256
  `f7a30e7bb99a279f27eb82da761917ef110b282d305e4bb7b40c5c2062e18772`.

Independent verify прошёл сначала без Docker, затем с `--load-images`; после
загрузки все четыре content ID совпали с manifest.

Содержимое `node/` скопировано в пустой каталог. Новый Compose project запущен
с пустыми volumes и флагами `--pull never --no-build`. За 85,6 секунды
завершились migration, node/identity bootstrap и demo seed; PostgreSQL, API,
worker, frontend и gateway стали healthy на отдельном порту. Проверены
`/health/live`, `/health/ready`, operational status, первый вход registrar и
signed journal. После приёмки containers, networks, volumes и тестовый private
key удалены.

## Что остаётся открытым

- production key ceremony и независимая доставка public key fingerprint;
- ручное решение по 160 `review_required` лицензиям;
- remote CI result на конкретном commit;
- interrupted update/rollback и restore на целевом резервном host;
- внешний security review и incident drill;
- юридические, организационные и pilot-критерии общего readiness checklist.

Подробный операторский процесс: [release_runbook.md](release_runbook.md).