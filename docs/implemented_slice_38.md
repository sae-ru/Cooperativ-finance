# Реализованный Slice 38: ответственность внешнего узла

Статус: code-level assurance для допуска внешнего узла, лимитов, обеспечения,
инцидентов, ключей и offline authority реализован и проверен.

Канонический software gate «каждая critical command имеет
actor/role/scope/evidence/exposure» закрыт для текущего реестра из 112 событий.
Это не закрывает юридические, организационные и пилотные production gates.

## Персональная ответственность узла

Fail-closed registry расширен с 87 до 112 событий. Onboarding внешнего узла
сохраняет заявление, принятие пяти именованных ролей, проверку личности,
challenge и независимый аудит. Каждое событие фиксирует локальный узел как
представляемую сторону, внешний узел и его действующих ответственных как
следующих владельцев шага.

Trust contract, bilateral limit и node bond подписывают proposer/activator,
точный предел потерь и единицу. Активация, приостановка, quarantine, revoke и
ограниченная реабилитация не теряют персональную цепочку ответственности.

## Инциденты, ключи и offline

- инцидент содержит evidence, тип, severity и независимое решение;
- плановая ротация ключа требует доказательства старого и нового ключей;
- после компрометации допускается continuity proof нового ключа и независимое
  approval;
- request, approve и reject сохраняют разных участников;
- offline epoch имеет явный внешний узел, лимиты, opener и reconciliation;
- резерв внешней exposure подписывает точное количество, единицу и активный
  bilateral limit;
- закрыть безымянный внешний offline epoch нельзя.

## Автоматическая проверка

- AST gate связывает все 112 event types с реальными `assurance=` call sites;
- чистый critical-quality: `35 passed`;
- journal verification quality-БД: `452/452`, failures отсутствуют;
- migration cycle: `0034 -> 0037 -> 0034 -> 0037`;
- Ruff: без ошибок;
- strict mypy: без ошибок в 220 production source files;
- полный backend: `256 passed, 1 deselected`;
- three-node federation acceptance: `1 passed`;
- live Docker node: `READY`, API и worker healthy;
- live journal: `434/434`, failures отсутствуют;
- evidence: `evidence/quality-20260728T170948Z`.

## Остаточный production scope

Исторические события до assurance v2 не переписываются. Registry является
каноническим software scope текущей версии, а не утверждением, что любое
подписанное служебное событие экономически критично.

Реальный запуск всё ещё требует принятых policy, юридической проверки,
проверенных начальных остатков и резервов, независимого аудита ключей,
обученных ответственных, резервного копирования и кризисных учений.
