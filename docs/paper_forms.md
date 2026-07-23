# Бумажные формы и аварийный контур

Статус: template catalog; юридическая форма утверждается пилотом.

## Общие реквизиты

Каждая форма содержит:

- form type и version;
- уникальный serial;
- node/cooperative и offline epoch;
- дата/место;
- stable identifiers участников и объектов;
- количество, unit, scale и качество;
- понятное утверждение подписанта;
- роль, scope и организация;
- maximum exposure, если применимо;
- evidence/attachments list;
- подписи и время;
- QR с минимальным reference и checksum, без чувствительной PII;
- поле digital event id после ввода.

Пустые бланки нумеруются и учитываются. Испорченный serial не используется
повторно.

## Каталог форм

| Код | Форма |
|---|---|
| PF-01 | регистрация участника и принятие правил |
| PF-02 | назначение/передача критической роли |
| PF-03 | приём партии |
| PF-04 | независимая проверка качества/количества |
| PF-05 | резервирование и выпуск товарного права |
| PF-06 | передача сохранности |
| PF-07 | выдача и погашение права |
| PF-08 | сделка/спецификация обязательства |
| PF-09 | частичное/полное исполнение |
| PF-10 | поручительство и паевая экспозиция |
| PF-11 | открытие спора/жалобы |
| PF-12 | решение и апелляция |
| PF-13 | solidarity contribution |
| PF-14 | allocation/delivery помощи |
| PF-15 | crisis activation/rationing exception |
| PF-16 | inventory discrepancy/incident |
| PF-17 | key/node emergency action |
| PF-18 | протокол восстановления и reconciliation |

## Выдача и хранение

- журнал диапазонов serial по ответственным;
- protected storage чистых и заполненных форм;
- custody transfer пачки форм;
- отдельное хранение sensitive forms;
- периодическая сверка использованных, свободных и испорченных serial;
- резервные копии шаблонов доступны без системы.

## Возврат в цифровой журнал

1. Оператор выбирает form type/version и вводит serial.
2. Система проверяет duplicate и epoch.
3. Поля вводятся двумя проходами или проходят independent review.
4. Скан сохраняется как evidence blob.
5. Подписанты связываются со stable member/role ids.
6. Создаётся `paper_operation_recorded` и соответствующая domain command.
7. Конфликт с цифровой операцией открывает case, не auto-overwrite.
8. На бумаге или в register фиксируется digital event id.

## Ограничения

Бумажная форма не обходит лимит, protected amount, независимость ролей или
последующую апелляцию. Emergency exception имеет отдельное основание, предел и
review. QR не считается доказательством без подписей и сверки содержимого.

## Federation paper forms Slice 11

Федеративная форма отличается от локальной crisis form: она всегда связана с
конкретными external node и open offline epoch. Serial уникален в пределах узла,
QR reference глобально уникален, checksum защищает печатный реквизит, payload
получает canonical SHA-256. Issue и record выполняют разные люди; record требует
participant signatures и evidence id. Неиспользованный оригинал можно только
void с причиной, но нельзя удалить. Epoch close блокируется при любой форме
`ISSUED`.
