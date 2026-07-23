import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  FileCheck2,
  Gauge,
  KeyRound,
  Network,
  RefreshCw,
  ScrollText,
  ShieldCheck,
} from "lucide-react";

import { getOperationalSnapshot } from "./api/operations";
import { formatLocalDateTime } from "./shared/date-time";

type Row = readonly [label: string, value: number, detail: string];

function Health({ value }: { value: number }) {
  return <span className={`status ${value === 0 ? "good" : "warn"}`}>{value === 0 ? "Норма" : value}</span>;
}

function OperationalRows({ rows }: { rows: readonly Row[] }) {
  return (
    <div className="rows">
      {rows.map(([label, value, detail]) => (
        <div className="data-row" key={label}>
          <strong>{label}</strong>
          <span>{detail}</span>
          <Health value={value} />
        </div>
      ))}
    </div>
  );
}

export default function OperationsView() {
  const snapshot = useQuery({
    queryKey: ["operational-snapshot"],
    queryFn: getOperationalSnapshot,
    refetchInterval: 30_000,
  });

  if (snapshot.isPending) {
    return <div className="state" role="status"><RefreshCw className="spin" size={24} /><span>Загрузка</span></div>;
  }
  if (snapshot.isError || !snapshot.data) {
    return <div className="state error" role="alert"><Activity size={24} /><strong>Снимок недоступен</strong></div>;
  }

  const data = snapshot.data;
  const delivery: readonly Row[] = [
    ["Ожидают отправки", data.outbox_pending, "События в локальной очереди outbox"],
    ["В карантине", data.outbox_quarantined, "Сообщения, требующие ручного разбора"],
    ["Открытые offline-эпохи", data.open_offline_epochs, "Локальные периоды автономной работы"],
  ];
  const federation: readonly Row[] = [
    ["Конфликты синхронизации", data.open_sync_conflicts, "Открытые и обжалуемые конфликты"],
    ["Инциденты узлов", data.open_node_incidents, "Открытые и локализованные инциденты"],
    ["Ротации ключей", data.pending_key_rotations, "Заявки, ожидающие решения"],
    ["Бумажные формы", data.issued_federation_forms, "Выданные, но ещё не погашенные формы"],
  ];
  const governance: readonly Row[] = [
    ["Споры", data.open_trust_cases, "Незакрытые дела доверия"],
    ["Апелляции", data.submitted_appeals, "Поданные апелляции"],
    ["Кризисные мандаты", data.active_crisis_mandates, "Действующие особые полномочия"],
    ["Кризисные формы", data.issued_crisis_forms, "Выданные бумажные формы"],
  ];

  return (
    <div className="view-stack operations-view">
      <header className="view-header">
        <div>
          <span className="eyebrow">Наблюдаемость</span>
          <h1>Эксплуатация узла</h1>
          <p>Снимок от {formatLocalDateTime(data.generated_at)}</p>
        </div>
        <span className="release">Схема<br />{data.schema_revision}</span>
      </header>
      <section className="metric-grid" aria-label="Эксплуатационная сводка">
        <article className="metric"><ScrollText size={18} /><span>События</span><strong>{data.signed_events}</strong></article>
        <article className="metric"><Gauge size={18} /><span>Outbox</span><strong>{data.outbox_pending}</strong></article>
        <article className="metric"><KeyRound size={18} /><span>Сессии</span><strong>{data.active_sessions}</strong></article>
        <article className="metric"><Network size={18} /><span>Конфликты</span><strong>{data.open_sync_conflicts}</strong></article>
        <article className="metric"><AlertTriangle size={18} /><span>Инциденты</span><strong>{data.open_node_incidents}</strong></article>
        <article className="metric"><FileCheck2 size={18} /><span>Формы</span><strong>{data.issued_federation_forms + data.issued_crisis_forms}</strong></article>
      </section>
      <section className="panel"><div className="panel-heading"><h2>Доставка событий</h2><ShieldCheck size={17} /></div><OperationalRows rows={delivery} /></section>
      <section className="panel"><div className="panel-heading"><h2>Федерация</h2><Network size={17} /></div><OperationalRows rows={federation} /></section>
      <section className="panel"><div className="panel-heading"><h2>Ответственность и кризис</h2><AlertTriangle size={17} /></div><OperationalRows rows={governance} /></section>
    </div>
  );
}
