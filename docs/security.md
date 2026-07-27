# Безопасность и управление ключами

Статус: обязательный security baseline.

## Цели

- не позволить одному аккаунту или администратору незаметно переписать
  хозяйственную историю;
- ограничить ущерб компрометированного ключа временем, scope и лимитом;
- сохранить локальный вход при отсутствии внешних сервисов;
- обеспечить восстановление без хранения открытых секретов в backup;
- минимизировать PII в журнале, пакетах и диагностике.

## Криптографический профиль

До юридического требования иного сертифицированного профиля:

- подписи: Ed25519;
- хеши: SHA-256;
- password hashing: Argon2id с параметрами, измеренными на целевом узле;
- AEAD: AES-256-GCM или ChaCha20-Poly1305 через поддерживаемую библиотеку;
- CSPRNG: только системный;
- canonical JSON: один версионированный профиль;
- TLS: локальная CA или сертификаты, не зависящие от внешнего ACME.

Самостоятельная реализация примитивов запрещена. Параметры фиксируются ADR и
test vectors.

## Типы ключей

| Ключ | Назначение | Хранение |
|---|---|---|
| Node signing | события и sync packages | TPM/OS keystore/encrypted keystore |
| Operator signing | критические действия человека | device-bound либо encrypted personal container |
| TLS | локальный HTTPS | host secret store |
| Backup encryption | backup set | отдельно от backup media |
| Distribution signing | релизы | offline release workstation |
| Recovery share | восстановление master material | разделённо у независимых хранителей |

Один ключ не используется для нескольких назначений.

## Жизненный цикл ключа

`GENERATED -> ACTIVE -> ROTATING -> RETIRED`

Инцидентные состояния: `SUSPENDED`, `REVOKED`, `COMPROMISED`.

Для ключа хранятся algorithm, public key, owner, scope, valid period, issuer,
status, rotation link и revocation evidence. Private material в БД отсутствует.

Ротация создаёт период совместной проверки старого и нового ключа. Отзыв
распространяется подписанным offline package. Emergency revoke требует двойного
контроля, кроме временной автоматической приостановки с последующим review.

## Сессии и доступ

- локальная учётная запись обязательна;
- refresh session хранится server-side и отзывается;
- короткий access token не содержит долгоживущих полномочий;
- роль проверяется по БД на момент критической команды;
- TOTP seed зашифрован AES-256-GCM отдельным `mfa_encryption_key`; seed никогда
  не возвращается после незавершённого enrollment;
- TOTP имеет окно ±30 секунд, запрет повторного moving counter, локальный
  brute-force lock и audit отказов;
- step-up хранится в server-side session, по умолчанию действует 10 минут и
  требуется для ключей, кризиса, взыскания, финализации клиринга и security policy;
- финализация local/inter-node клиринга не допускает break-glass bypass;
- recovery доступа требует двух независимых персональных сотрудников, отзывает
  старые сессии/TOTP и создаёт подписанное событие;
- break-glass ограничен allowlist роли, scope и сроком 15-60 минут; отдельный
  `source=BREAK_GLASS` не попадает в обычную выдачу ролей;
- временное право нельзя использовать для recovery, делегирования или
  превращения в постоянное; каждое HTTP-обращение с ним журналируется;
- WebAuthn остаётся обязательным расширением для production-профилей, где
  утверждена device-bound аутентификация.

## Secrets

Запрещены secrets в Git, container image, frontend bundle, обычном `.env`,
журналах, exception text и test fixtures. `.env` содержит только несекретные
настройки или путь к secret file. Production secrets монтируются с минимальными
filesystem permissions.

`mfa_encryption_key` генерируется отдельно, монтируется только backend-сервисам и входит в зашифрованный recovery material; потеря этого ключа делает активные TOTP seeds непригодными и требует контролируемого восстановления учётных записей.

Secret scanning запускается локально и в CI. Найденный опубликованный secret
считается скомпрометированным, а не просто удаляется из последнего commit.

## Application security

- parameterized SQL через ORM/Core;
- output encoding и строгая CSP;
- CSRF protection для cookie auth;
- allowlist CORS, hosts, content types и upload sizes;
- malware policy для вложений без обязательного облачного scanner;
- SSRF protection и отсутствие произвольных URL fetch в MVP;
- rate limits для login, export, upload, sync verify и expensive reports;
- dependency lock, SBOM, vulnerability review и signed distribution;
- runtime container без root, read-only filesystem где возможно;
- отдельные DB roles для migrations, runtime и backup;
- sensitive actions требуют reason и actor context.

## Privacy

- PII отделяется от хозяйственных идентификаторов;
- sync package содержит минимум данных для принимающего узла;
- публичный отчёт агрегируется и проверяется на малые группы;
- медицинские, whistleblower и recipient data имеют отдельные scopes;
- audit доступа к чувствительным данным сам является защищённой записью;
- удаление допустимой PII не уничтожает проверяемость хозяйственного события.

## Security logging

Фиксируются login, failed login, session revoke, role/key changes, exports,
чтение особо чувствительных case files, policy changes, sync reject, integrity
failure, backup/restore и break-glass. Пароли, tokens, private keys и полные
чувствительные payload не журналируются.

## Incident response

1. Ограничить session/key/node без удаления истории.
2. Сохранить volatile evidence и audit snapshot.
3. Определить scope и earliest compromise time.
4. Распространить revoke/quarantine package.
5. Проверить затронутые события и физические активы.
6. Выпустить compensations или dispute cases.
7. Восстановить ключи и доступ по двойному контролю.
8. Опубликовать допустимый audit report и corrective actions.

## Security gates

- threat model обновлён для slice;
- tests authorization matrix и object-level access;
- negative tests подписей, replay, race и escalation;
- dependency и secret scans без critical findings;
- backup restore drill;
- независимый review криптографии и liability path до пилота.
