import { Ban, ClipboardCheck, FilePenLine, QrCode } from "lucide-react";

import type { Principal, RoleCode } from "./api/admin";
import {
  issueFederationPaperForm,
  recordFederationPaperForm,
  voidFederationPaperForm,
  type FederationNode,
  type FederationPaperForm,
  type OfflineEpoch
} from "./api/federation";
import { formatLocalDateTime } from "./shared/date-time";

type RunAction = (action: () => Promise<unknown>) => void;

function hasRole(principal: Principal, ...roles: RoleCode[]) {
  return principal.roles.some((grant) => roles.includes(grant.role));
}

function localDate(hours: number) {
  const date = new Date(Date.now() + hours * 3_600_000);
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
}

export default function FederationPaperForms({
  nodes,
  epochs,
  forms,
  principal,
  run
}: {
  nodes: FederationNode[];
  epochs: OfflineEpoch[];
  forms: FederationPaperForm[];
  principal: Principal;
  run: RunAction;
}) {
  const openEpochs = epochs.filter((item) => item.status === "OPEN");
  const issued = forms.filter((item) => item.status === "ISSUED");
  const canIssue = hasRole(principal, "NODE_REGISTRAR", "NODE_BUSINESS_OPERATOR");
  const canRecord = hasRole(
    principal,
    "AUDITOR",
    "NODE_AUDITOR",
    "NODE_SECURITY_ADMIN",
    "SECURITY_ADMIN"
  );

  return (
    <>
      <section className="federation-command-band two-columns">
        {canIssue ? (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              const participants = String(form.get("participants"))
                .split(",")
                .map((item) => item.trim())
                .filter(Boolean);
              run(() =>
                issueFederationPaperForm(String(form.get("epoch")), {
                  serial_number: form.get("serial"),
                  form_type: form.get("type"),
                  form_version: 1,
                  participant_refs: participants,
                  operation_constraints: {
                    maximum_value: form.get("maximum"),
                    unit: form.get("unit"),
                    physical_reconciliation: true
                  },
                  expires_at: new Date(String(form.get("expires"))).toISOString()
                })
              );
            }}
          >
            <strong>
              <FilePenLine size={15} /> Выдать бланк
            </strong>
            <label>
              Offline-эпоха
              <select name="epoch" required>
                {openEpochs.map((item) => (
                  <option value={item.id} key={item.id}>
                    {nodes.find((node) => node.id === item.external_node_id)?.node_code ??
                      item.id.slice(0, 8)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Серийный номер
              <input name="serial" required maxLength={64} />
            </label>
            <label>
              Тип
              <select name="type">
                <option value="GOODS_TRANSFER">Передача товара</option>
                <option value="LOGISTICS_HANDOFF">Передача в логистике</option>
                <option value="SERVICE_ACCEPTANCE">Приёмка услуги</option>
                <option value="EMERGENCY_NODE_ACTION">Аварийное действие узла</option>
                <option value="EXCEPTION">Исключение</option>
              </select>
            </label>
            <label className="wide-field">
              Участники через запятую
              <input name="participants" required />
            </label>
            <label>
              Максимум
              <input name="maximum" type="number" min="0" step="any" required />
            </label>
            <label>
              Единица
              <input name="unit" defaultValue="UNIT" required />
            </label>
            <label>
              Действует до
              <input name="expires" type="datetime-local" defaultValue={localDate(2)} required />
            </label>
            <button className="primary-button" type="submit" disabled={!openEpochs.length}>
              <FilePenLine size={15} /> Выдать
            </button>
          </form>
        ) : null}

        {canRecord ? (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              const item = issued.find((candidate) => candidate.id === form.get("paper"));
              if (!item) return;
              run(() =>
                recordFederationPaperForm(item, {
                  operation_payload: {
                    resource: form.get("resource"),
                    quantity: form.get("quantity"),
                    unit: form.get("unit"),
                    occurred_at: new Date(String(form.get("occurred"))).toISOString()
                  },
                  signatures: item.participant_refs.map((party_ref) => ({
                    party_ref,
                    kind: "WET_INK"
                  })),
                  evidence_ids: [form.get("evidence")]
                })
              );
            }}
          >
            <strong>
              <ClipboardCheck size={15} /> Ввести оригинал
            </strong>
            <label>
              Бланк
              <select name="paper" required>
                {issued.map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.serial_number}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Ресурс
              <input name="resource" required />
            </label>
            <label>
              Количество
              <input name="quantity" type="number" min="0.000000000001" step="any" required />
            </label>
            <label>
              Единица
              <input name="unit" defaultValue="UNIT" required />
            </label>
            <label>
              Время операции
              <input name="occurred" type="datetime-local" defaultValue={localDate(0)} required />
            </label>
            <label className="wide-field">
              ID скана или доказательства
              <input name="evidence" required />
            </label>
            <button className="primary-button" type="submit" disabled={!issued.length}>
              <ClipboardCheck size={15} /> Зафиксировать
            </button>
          </form>
        ) : null}
      </section>

      <section className="panel">
        <div className="table-wrap">
          <table className="federation-table">
            <thead>
              <tr>
                <th>Бланк</th>
                <th>Узел и эпоха</th>
                <th>Тип</th>
                <th>Участники</th>
                <th>Состояние</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {forms.map((item) => (
                <tr key={item.id}>
                  <td>
                    <strong>{item.serial_number}</strong>
                    <small>{formatLocalDateTime(item.issued_at)}</small>
                    <code className="federation-hash" title={item.qr_reference}>
                      <QrCode size={13} /> {item.qr_reference}
                    </code>
                  </td>
                  <td>
                    {nodes.find((node) => node.id === item.external_node_id)?.node_code}
                    <small>{item.epoch_id.slice(0, 8)}</small>
                  </td>
                  <td>
                    {item.form_type}
                    <small>v{item.form_version}</small>
                  </td>
                  <td>{item.participant_refs.join(", ")}</td>
                  <td>
                    <span className={`status ${item.status === "RECORDED" ? "good" : item.status === "VOID" ? "bad" : "warn"}`}>
                      {item.status}
                    </span>
                    <small>{item.payload_hash ?? formatLocalDateTime(item.expires_at)}</small>
                  </td>
                  <td>
                    {item.status === "ISSUED" &&
                    hasRole(principal, "AUDITOR", "NODE_AUDITOR", "NODE_REGISTRAR") ? (
                      <button
                        title="Аннулировать"
                        onClick={() =>
                          run(() =>
                            voidFederationPaperForm(
                              item,
                              "Неиспользованный оригинал погашен независимым контролёром."
                            )
                          )
                        }
                      >
                        <Ban size={15} />
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
