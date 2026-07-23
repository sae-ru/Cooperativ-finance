import { Ban, Check, KeyRound } from "lucide-react";

import type { Principal, RoleCode } from "./api/admin";
import {
  decideNodeKeyRotation,
  requestNodeKeyRotation,
  type FederationNode,
  type NodeKeyRotation
} from "./api/federation";
import { formatLocalDateTime } from "./shared/date-time";

type RunAction = (action: () => Promise<unknown>) => void;

function hasRole(principal: Principal, ...roles: RoleCode[]) {
  return principal.roles.some((grant) => roles.includes(grant.role));
}

function localDate(days: number) {
  const date = new Date(Date.now() + days * 86_400_000);
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
}

export default function FederationKeyRotations({
  nodes,
  rotations,
  principal,
  run
}: {
  nodes: FederationNode[];
  rotations: NodeKeyRotation[];
  principal: Principal;
  run: RunAction;
}) {
  return (
    <>
      {hasRole(principal, "NODE_SECURITY_ADMIN", "SECURITY_ADMIN") ? (
        <section className="federation-command-band">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              const oldSignature = String(form.get("old_signature")).trim();
              run(() =>
                requestNodeKeyRotation(String(form.get("node")), {
                  new_public_key_base64: form.get("public_key"),
                  valid_from: new Date(String(form.get("valid_from"))).toISOString(),
                  valid_until: new Date(String(form.get("valid_until"))).toISOString(),
                  reason: form.get("reason"),
                  old_signature_base64: oldSignature || null,
                  new_signature_base64: form.get("new_signature")
                })
              );
            }}
          >
            <strong>
              <KeyRound size={15} /> Ротация ключа узла
            </strong>
            <label>
              Узел
              <select name="node" required>
                {nodes.map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.node_code}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Причина
              <select name="reason">
                <option value="SCHEDULED">Плановая</option>
                <option value="CUSTODY_CHANGE">Смена хранителя</option>
                <option value="RECOVERY">Восстановление</option>
                <option value="COMPROMISE">Компрометация</option>
              </select>
            </label>
            <label>
              Действует с
              <input name="valid_from" type="datetime-local" defaultValue={localDate(0)} required />
            </label>
            <label>
              Действует до
              <input name="valid_until" type="datetime-local" defaultValue={localDate(365)} required />
            </label>
            <label className="wide-field">
              Новый публичный ключ Ed25519, base64
              <textarea name="public_key" required />
            </label>
            <label className="wide-field">
              Подпись старым ключом, base64
              <textarea name="old_signature" />
            </label>
            <label className="wide-field">
              Подпись новым ключом, base64
              <textarea name="new_signature" required />
            </label>
            <button className="primary-button" type="submit" disabled={!nodes.length}>
              <KeyRound size={15} /> Запросить
            </button>
          </form>
        </section>
      ) : null}

      <section className="panel">
        <div className="table-wrap">
          <table className="federation-table">
            <thead>
              <tr>
                <th>Запрос</th>
                <th>Узел</th>
                <th>Причина</th>
                <th>Непрерывность</th>
                <th>Состояние</th>
                <th>Решение</th>
              </tr>
            </thead>
            <tbody>
              {rotations.map((item) => (
                <tr key={item.id}>
                  <td>
                    <strong>{item.id.slice(0, 8)}</strong>
                    <small>{formatLocalDateTime(item.created_at)} · v{item.version}</small>
                  </td>
                  <td>{nodes.find((node) => node.id === item.node_id)?.node_code}</td>
                  <td>{item.reason}</td>
                  <td>{item.continuity_verified ? "Подтверждена" : "Аварийная замена"}</td>
                  <td>
                    <span className={`status ${item.status === "APPROVED" ? "good" : item.status === "REJECTED" ? "bad" : "warn"}`}>
                      {item.status}
                    </span>
                  </td>
                  <td>
                    {item.status === "PENDING" &&
                    hasRole(principal, "NODE_REGISTRAR", "AUDITOR", "NODE_AUDITOR") ? (
                      <div className="table-actions">
                        <button
                          title="Одобрить ротацию"
                          onClick={() => run(() => decideNodeKeyRotation(item, true))}
                        >
                          <Check size={15} />
                        </button>
                        <button
                          title="Отклонить ротацию"
                          onClick={() => run(() => decideNodeKeyRotation(item, false))}
                        >
                          <Ban size={15} />
                        </button>
                      </div>
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
