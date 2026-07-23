# Концепция / Concept

[Русский](#русский) | [English](#english) | [Главная / Home](../README.md)

## Русский

### Зачем нужна система

Cooperative Clearing предназначена для реального обмена товарами, работами и услугами внутри сети самостоятельных сообществ. Ее задача не создать еще одну спекулятивную валюту, а связать спрос, физическое предложение, доставку, обязательства, доказательства исполнения и ответственность конкретных людей.

В обычном режиме система снижает трение между участниками. В кризисном режиме она позволяет локальным узлам продолжать учет и обмен при плохой связи, недоступности банков или распаде части федерации. Каждый узел владеет своими данными и может работать автономно, но принимает только подписанные и проверяемые события других узлов.

### Основные принципы

1. **Товар и исполнение первичны.** Запись не заменяет молоко, гвозди, труд или доставку. Она подтверждает, кто, что и на каких условиях обещал и исполнил.
2. **Local-first.** Узел сохраняет работоспособность без центрального сервера и постоянного Интернета.
3. **Проверяемая федерация.** Узлы обмениваются подписанными каталогами, резервами, клиринговыми снимками и сертификатами завершения.
4. **Персональная ответственность.** Для значимого действия известны инициатор, исполнитель, хранитель, проверяющий и одобряющий. Ответственность принимается явно и ограничена по предмету, сроку и сумме.
5. **Паи не являются автоматическим штрафом.** Часть пая может быть заранее заблокирована как обеспечение принятого риска. Списание возможно только по утвержденной процедуре с доказательствами, причинной связью и правом апелляции.
6. **Репутация контекстна.** Нет одного числа, определяющего ценность человека. Надежность рассматривается отдельно для поставки, хранения, качества, логистики, поручительства и других функций.
7. **Помощь отделена от торговли.** Добровольный вклад не создает долг получателя, не покупает влияние и не повышает коммерческую репутацию.
8. **Клиринг детерминирован.** Один и тот же подписанный набор входных данных дает одинаковый результат на любом проверяющем узле.
9. **Исправление через новые события.** Подписанная история не переписывается. Ошибки закрываются компенсирующими операциями и решениями по спору.
10. **Человек сохраняет право решения.** Система показывает последствия и блокирует нарушение правил, но не выносит автоматические имущественные наказания.

### Как возникает сделка

Покупатель формирует спрос. Узлы возвращают подписанные предложения товара, а логистические узлы добавляют маршруты и стоимость доставки. Кандидаты сортируются по полной цене, сроку, свежести подписи и допустимым ограничениям. Выбранные товар и доставка резервируются. После независимых подтверждений обязательства могут войти в клиринговый цикл или завершиться прямым исполнением.

Клиринг ищет замкнутые и частично взаимозачетные цепочки обязательств. Он уменьшает объем окончательного расчета, но не создает отсутствующий товар и не скрывает дефицит. Если цепочка не собирается, обязательство остается открытым, дробится только по разрешенным правилам либо истекает без ложного подтверждения.

### Реалистичные границы

Система не гарантирует выживание сообщества и не заменяет право, физическую охрану складов, контроль качества, транспорт, связь или общественное доверие. Для реального запуска нужны утвержденные положения кооператива, аудит начальных остатков, обученные ответственные лица, резервные копии, бумажные процедуры и регулярные кризисные учения.

## English

### Purpose

Cooperative Clearing supports real exchange of goods, labor, and services across a network of autonomous communities. Its goal is not to create another speculative currency. It connects demand, physical supply, delivery, obligations, execution evidence, and the responsibility of named people.

In normal conditions the system reduces coordination friction. During a crisis, local nodes can continue recording and exchanging value despite weak connectivity, unavailable banks, or a fragmented federation. Each node owns its data and can operate independently, while accepting only signed and verifiable events from other nodes.

### Core principles

1. **Goods and execution come first.** A record does not replace milk, nails, labor, or delivery. It proves who promised and performed what, under which terms.
2. **Local-first operation.** A node remains useful without a central server or permanent Internet access.
3. **Verifiable federation.** Nodes exchange signed catalogs, reservations, clearing snapshots, and completion certificates.
4. **Personal responsibility.** Significant actions name the initiator, performer, custodian, reviewer, and approver. Responsibility is explicitly accepted and bounded by subject, time, and amount.
5. **Shares are not an automatic penalty.** A portion may be locked in advance as collateral for an accepted risk. Any loss allocation requires an approved procedure, evidence, causation, and an appeal path.
6. **Reputation is contextual.** No single score defines a person. Reliability is assessed separately for supply, storage, quality, logistics, guarantees, and other duties.
7. **Aid is separate from trade.** A voluntary contribution creates no recipient debt, buys no influence, and does not improve commercial reputation.
8. **Clearing is deterministic.** The same signed input set produces the same result on every verifier node.
9. **Corrections use new events.** Signed history is not rewritten. Errors are resolved through compensating operations and dispute decisions.
10. **People retain judgment.** The system previews consequences and enforces approved rules, but does not issue automatic property penalties.

### How a transaction forms

A buyer publishes demand. Nodes return signed product offers, while logistics nodes add routes and delivery quotes. Candidates are ranked by delivered cost, timing, signature freshness, and applicable constraints. The selected goods and delivery are reserved. After independent confirmations, obligations may enter a clearing cycle or settle through direct execution.

Clearing searches for closed and partially offsetting obligation chains. It reduces final settlement volume but cannot create missing goods or conceal scarcity. When no chain can be assembled, an obligation remains open, may be split only under allowed rules, or expires without a false completion state.

### Realistic boundaries

The system cannot guarantee a community's survival and does not replace law, physical warehouse security, quality control, transport, communications, or social trust. Real deployment requires approved cooperative policies, an audit of opening balances, trained responsible people, backups, paper procedures, and regular crisis exercises.