# Runbook пилота

Статус: operational template для ограниченного шестимесячного пилота.

## Цель

Проверить не интерфейс, а реальный хозяйственный цикл, ограничение риска,
понимание участниками, восстановление и справедливое разрешение ошибок.

## Entry criteria

- production readiness checklist принят для pilot release;
- OD-001..034 закрыты в объёме используемых features;
- 50-200 участников и 5-10 товаров/услуг подтверждены;
- склад, контролёр, логист, аудит, арбитраж и guarantor назначены;
- ручной процесс пройден минимум один раз;
- договоры и бумажные формы выданы;
- reserve host, UPS, backup media и restore drill готовы;
- обучение и тестовые сценарии завершены;
- stop conditions опубликованы.

## Роли смены

- business operator;
- warehouse custodian;
- independent controller;
- security/technical operator;
- backup custodian;
- on-call domain owner;
- audit contact;
- incident decision group.

Дежурный технический оператор не получает право единолично менять хозяйственную
policy.

## Запуск

1. Зафиксировать release/node/policy versions.
2. Проверить время, keys/certificates и revocation freshness.
3. Выполнить opening inventory и share balances reconciliation.
4. Проверить локальный вход каждой критической роли.
5. Создать контрольный signed event и backup.
6. Открыть pilot period подписанным протоколом.

## Ежедневно

- health, disk, clock, worker, backup;
- pending approvals и expired roles;
- stock discrepancies и custody gaps;
- unresolved sync/security alerts;
- crisis/reserve thresholds;
- complaints и privacy incidents.

## Еженедельный цикл

1. Inventory sample и discrepancy review.
2. Review overdue obligations и voluntary cure.
3. Build clearing input и publish preview.
4. Dispute window.
5. Finalize eligible clearing cycle.
6. Reconcile shares/exposures and solidarity fund.
7. Export accounting documents.
8. Create offline backup copy.
9. Publish internal audit summary.

## Инциденты

Severity определяется ущербом, scope, integrity, availability и privacy.
Critical incident приостанавливает затронутый workflow, но по возможности не
останавливает независимые локальные операции.

Для каждого incident: owner, containment, evidence, affected events/assets,
communication, recovery, compensation, root cause и corrective action.

## Stop conditions

- невозможно доказать реальный товарный остаток;
- систематическое превышение паевой экспозиции;
- невосстановимый signed journal или backup;
- массовое непонимание обязательств участниками;
- отсутствие независимой апелляции/аудита;
- тяжёлая утечка данных без containment;
- повторяющаяся помощь не достигает получателей;
- юридическое требование остановки;
- критический defect не имеет безопасного workaround.

Stop означает запрет новых рисков и controlled wind-down, а не удаление данных.

## Метрики

Измеряются реальные сделки, исполнения, clearing share, overdue/default,
inventory discrepancy, dispute SLA, restore RTO, offline duration, support
burden, participant comprehension, aid delivery и false positive controls.

Метрика не используется как универсальный score человека.

## Завершение пилота

Закрываются pending operations, выполняются inventory/accounting reconciliation,
backup/restore verification, participant survey, independent audit и report о
residual risks. Решение об expansion принимается отдельно от команды разработки.
