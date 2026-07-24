import {
  ArrowRightLeft,
  BadgeCheck,
  Boxes,
  ClipboardCheck,
  FileCheck2,
  FileKey2,
  LockKeyhole,
  PackageCheck,
  Printer,
  RefreshCw,
  ShieldAlert,
  UnlockKeyhole,
  X,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useMemo, useState } from "react";

import { AdminApiError, type Principal, type RoleCode } from "./api/admin";
import {
  getInventoryMembers,
  getLots,
  getProducts,
  getUnits,
  getWarehouses,
  uploadEvidence,
} from "./api/inventory";
import {
  completeRightRedemption,
  freezeCommodityRight,
  getCommodityRights,
  getLotBalances,
  getRightProof,
  getRightRedemptions,
  issueCommodityRight,
  requestRightRedemption,
  transferCommodityRight,
  unfreezeCommodityRight,
  type CommodityRight,
  type RightProof,
} from "./api/rights";
import { userErrorMessage } from "./shared/api-error";
import { formatLocalDateTime } from "./shared/date-time";
import "./rights.css";

type Section = "registry" | "backing" | "issue" | "operations" | "fulfill" | "control";

const statusNames: Record<string, string> = {
  ISSUED: "Выпущено",
  TRANSFERRED: "Передано",
  REDEMPTION_PENDING: "Ожидает выдачи",
  FROZEN: "Заморожено",
  REDEEMED: "Погашено",
  EXPIRED: "Истекло",
  CANCELLED_BY_COMPENSATION: "Компенсировано",
  REQUESTED: "Запрошено",
  COMPLETED: "Выдано",
};

function hasRole(principal: Principal, ...roles: RoleCode[]): boolean {
  return principal.roles.some((grant) => roles.includes(grant.role));
}

function errorText(error: unknown): string {
  return userErrorMessage(error);
}

function StatusPill({ value }: { value: string }) {
  const kind = ["ISSUED", "TRANSFERRED", "REDEEMED", "COMPLETED"].includes(value)
    ? "good"
    : ["FROZEN", "EXPIRED", "CANCELLED_BY_COMPENSATION"].includes(value)
      ? "bad"
      : "warn";
  return <span className={`status ${kind}`}>{statusNames[value] ?? value}</span>;
}

function shortId(value: string): string {
  return value.slice(0, 8);
}

function displayQuantity(value: string, scale: number): string {
  const [integer = "0", fraction = ""] = value.split(".");
  if (scale === 0) return integer;
  return `${integer}.${fraction.padEnd(scale, "0").slice(0, scale)}`;
}

function ProofDialog({ proof, onClose }: { proof: RightProof; onClose: () => void }) {
  return (
    <dialog className="receipt-dialog right-proof-dialog" open aria-labelledby="right-proof-title">
      <article className="receipt-act">
        <header className="receipt-toolbar no-print">
          <strong>Доказательство товарного права</strong>
          <span className="icon-actions">
            <button title="Печать" onClick={() => window.print()}><Printer size={17} /></button>
            <button title="Закрыть" onClick={onClose}><X size={17} /></button>
          </span>
        </header>
        <div className="receipt-body">
          <header className="receipt-title">
            <div><span>Cooperative Clearing</span><h1 id="right-proof-title">Товарное право</h1></div>
            <div><strong>{shortId(proof.right.id)}</strong><span>{formatLocalDateTime(proof.generated_at)}</span></div>
          </header>
          <section className="receipt-grid">
            <div><span>Партия</span><strong>{proof.lot_number}</strong><small>{shortId(proof.right.lot_id)}</small></div>
            <div><span>Количество</span><strong>{proof.right.quantity}</strong><small>резерв {shortId(proof.reservation.id)}</small></div>
            <div><span>Текущий владелец</span><strong>{proof.current_owner_name}</strong><small>первый: {proof.original_owner_name}</small></div>
            <div><span>Статус</span><StatusPill value={proof.right.status} /></div>
          </section>
          <section className="proof-balance">
            <div><span>Подтверждено</span><strong>{proof.balance.verified_quantity}</strong></div>
            <div><span>Доступно</span><strong>{proof.balance.available_quantity}</strong></div>
            <div><span>В правах</span><strong>{proof.balance.rights_issued_quantity}</strong></div>
            <div><span>Дефицит</span><strong>{proof.balance.backing_shortfall_quantity}</strong></div>
          </section>
          <section className="receipt-section">
            <h2>Передачи владельца</h2>
            {proof.transfers.length === 0 ? <p className="proof-empty">Передач не было</p> : (
              <div className="table-wrap"><table><thead><tr><th>Время</th><th>От</th><th>Кому</th><th>Событие</th></tr></thead><tbody>{proof.transfers.map((item) => <tr key={item.id}><td>{formatLocalDateTime(item.created_at)}</td><td>{shortId(item.from_member_id)}</td><td>{shortId(item.to_member_id)}</td><td><code>{item.event_id}</code></td></tr>)}</tbody></table></div>
            )}
          </section>
          <section className="receipt-section">
            <h2>Подписанная цепочка</h2>
            <div className="table-wrap"><table><thead><tr><th>№</th><th>Событие</th><th>Агрегат</th><th>Хеш</th></tr></thead><tbody>{proof.signed_events.map((item) => <tr key={item.event_id}><td>{item.local_sequence}</td><td><strong>{item.event_type}</strong><small>{formatLocalDateTime(item.occurred_at)}</small></td><td>{item.aggregate_type}<small>v{item.aggregate_version}</small></td><td><code>{item.event_hash}</code></td></tr>)}</tbody></table></div>
          </section>
          <footer className="proof-hash"><span>Proof SHA-256</span><code>{proof.proof_hash}</code></footer>
        </div>
      </article>
    </dialog>
  );
}

export default function RightsView({ principal }: { principal: Principal }) {
  const queryClient = useQueryClient();
  const [section, setSection] = useState<Section>("registry");
  const [proof, setProof] = useState<RightProof | null>(null);
  const balances = useQuery({ queryKey: ["right-balances"], queryFn: getLotBalances });
  const rights = useQuery({ queryKey: ["commodity-rights"], queryFn: getCommodityRights });
  const redemptions = useQuery({ queryKey: ["right-redemptions"], queryFn: getRightRedemptions });
  const lots = useQuery({ queryKey: ["inventory-lots"], queryFn: getLots });
  const products = useQuery({ queryKey: ["inventory-products"], queryFn: getProducts });
  const units = useQuery({ queryKey: ["inventory-units"], queryFn: getUnits });
  const warehouses = useQuery({ queryKey: ["inventory-warehouses"], queryFn: getWarehouses });
  const members = useQuery({ queryKey: ["inventory-members"], queryFn: getInventoryMembers });
  const canIssue = hasRole(principal, "RIGHTS_OPERATOR", "COOPERATIVE_ADMIN");
  const canControl = hasRole(principal, "RISK_ADMIN", "AUDITOR");
  const canFulfill = hasRole(principal, "WAREHOUSE_CUSTODIAN");
  const rightData = rights.data ?? [];
  const balanceData = balances.data ?? [];
  const lotData = lots.data ?? [];
  const unitData = units.data ?? [];
  const memberData = members.data ?? [];
  const productData = products.data ?? [];
  const warehouseData = warehouses.data ?? [];
  const redemptionData = redemptions.data ?? [];
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["right-balances"] }),
      queryClient.invalidateQueries({ queryKey: ["commodity-rights"] }),
      queryClient.invalidateQueries({ queryKey: ["right-redemptions"] }),
      queryClient.invalidateQueries({ queryKey: ["inventory-lots"] }),
    ]);
  };
  const proofMutation = useMutation({ mutationFn: getRightProof, onSuccess: setProof });
  const loading = [balances, rights, redemptions, lots, products, units, warehouses, members]
    .some((query) => query.isLoading);
  const failed = [balances, rights, redemptions, lots, products, units, warehouses, members]
    .find((query) => query.isError);
  const sections: Array<[Section, string, typeof Boxes]> = [
    ["registry", "Права", FileKey2],
    ["backing", "Обеспечение", Boxes],
  ];
  if (canIssue) sections.push(["issue", "Выпуск", BadgeCheck], ["operations", "Оборот", ArrowRightLeft]);
  if (canFulfill) sections.push(["fulfill", "Выдача", PackageCheck]);
  if (canControl) sections.push(["control", "Контроль", ShieldAlert]);

  if (loading) return <div className="view-stack rights-view"><div className="state" role="status"><RefreshCw className="spin" size={24} /><span>Загрузка прав</span></div></div>;
  if (failed) return <div className="view-stack rights-view"><div className="state error" role="alert">{errorText(failed.error)}</div></div>;

  const issuedTotal = rightData.filter((item) => item.status !== "REDEEMED").length;
  const pendingTotal = redemptionData.filter((item) => item.status === "REQUESTED").length;
  const frozenTotal = rightData.filter((item) => item.status === "FROZEN").length;
  const shortfallTotal = balanceData.filter((item) => Number(item.backing_shortfall_quantity) > 0).length;

  return (
    <div className="view-stack rights-view">
      <header className="view-header"><div><span className="eyebrow">ОБЕСПЕЧЕННЫЙ КОНТУР</span><h1>Товарные права</h1><p>Резерв, передача владельца и двухфазная выдача товара</p></div><div className="section-tabs">{sections.map(([key, label, Icon]) => <button className={section === key ? "active" : ""} onClick={() => setSection(key)} key={key}><Icon size={15} /><span>{label}</span></button>)}</div></header>
      <section className="metric-grid rights-metrics" aria-label="Сводка прав">
        <article className="metric"><FileCheck2 size={20} /><span>Действуют</span><strong>{issuedTotal}</strong></article>
        <article className="metric"><ClipboardCheck size={20} /><span>Ждут выдачи</span><strong>{pendingTotal}</strong></article>
        <article className="metric"><LockKeyhole size={20} /><span>Заморожено</span><strong>{frozenTotal}</strong></article>
        <article className="metric"><ShieldAlert size={20} /><span>Дефицит обеспечения</span><strong>{shortfallTotal}</strong></article>
      </section>
      {section === "registry" ? <section className="panel"><div className="panel-heading"><h2>Реестр товарных прав</h2><span>{rightData.length}</span></div><div className="table-wrap"><table className="rights-table"><thead><tr><th>Статус</th><th>Право и партия</th><th>Владелец</th><th>Количество</th><th>Место выдачи</th><th>Proof</th></tr></thead><tbody>{rightData.map((right) => { const lot = lotData.find((item) => item.id === right.lot_id); const unit = unitData.find((item) => item.id === right.unit_id); return <tr key={right.id}><td><StatusPill value={right.status} /><small>{formatLocalDateTime(right.updated_at)}</small></td><td><strong>{shortId(right.id)}</strong><small>{lot?.lot_number ?? right.lot_id}</small></td><td><strong>{memberData.find((item) => item.member_id === right.owner_member_id)?.display_name ?? right.owner_member_id}</strong><small>v{right.version}</small></td><td><strong>{displayQuantity(right.quantity, unit?.decimal_scale ?? 12)} {unit?.symbol}</strong><small>резерв {shortId(right.reservation_id)}</small></td><td><strong>{warehouseData.find((item) => item.id === right.redeem_warehouse_id)?.name ?? right.redeem_warehouse_id}</strong><small>{right.valid_until ? `до ${formatLocalDateTime(right.valid_until)}` : "без срока"}</small></td><td><button className="icon-button" title="Открыть proof" onClick={() => proofMutation.mutate(right.id)}><Printer size={16} /></button></td></tr>; })}</tbody></table></div></section> : null}
      {section === "backing" ? <section className="panel"><div className="panel-heading"><h2>Доступное обеспечение</h2><span>{balanceData.length}</span></div><div className="table-wrap"><table className="backing-table"><thead><tr><th>Партия</th><th>Товар</th><th>Подтверждено</th><th>Доступно</th><th>В правах</th><th>Карантин / дефицит</th></tr></thead><tbody>{balanceData.map((balance) => { const lot = lotData.find((item) => item.id === balance.lot_id); const product = productData.find((item) => item.id === lot?.product_id); const unit = unitData.find((item) => item.id === lot?.unit_id); const scale = unit?.decimal_scale ?? 12; return <tr key={balance.lot_id}><td><strong>{lot?.lot_number ?? shortId(balance.lot_id)}</strong><small>v{balance.version} · {formatLocalDateTime(balance.updated_at)}</small></td><td><strong>{product?.name ?? lot?.product_id}</strong><small>{warehouseData.find((item) => item.id === lot?.warehouse_id)?.name}</small></td><td>{displayQuantity(balance.verified_quantity, scale)} {unit?.symbol}</td><td><strong>{displayQuantity(balance.available_quantity, scale)} {unit?.symbol}</strong></td><td>{displayQuantity(balance.rights_issued_quantity, scale)} {unit?.symbol}</td><td><strong>{displayQuantity(balance.quarantined_quantity, scale)} / {displayQuantity(balance.backing_shortfall_quantity, scale)}</strong></td></tr>; })}</tbody></table></div></section> : null}
      {section === "issue" ? <IssueForm balances={balanceData} lots={lotData} members={memberData} products={productData} warehouses={warehouseData} onDone={refresh} /> : null}
      {section === "operations" ? <OperationsForm cooperativeId={rightData[0]?.cooperative_id ?? lotData[0]?.cooperative_id ?? ""} rights={rightData} members={memberData} onDone={refresh} /> : null}
      {section === "fulfill" ? <FulfillmentForm cooperativeId={rightData[0]?.cooperative_id ?? lotData[0]?.cooperative_id ?? ""} rights={rightData} redemptions={redemptionData} onDone={refresh} /> : null}
      {section === "control" ? <ControlForm rights={rightData} onDone={refresh} /> : null}
      {proof ? <ProofDialog proof={proof} onClose={() => setProof(null)} /> : null}
    </div>
  );
}

function IssueForm({ balances, lots, members, products, warehouses, onDone }: { balances: Awaited<ReturnType<typeof getLotBalances>>; lots: Awaited<ReturnType<typeof getLots>>; members: Awaited<ReturnType<typeof getInventoryMembers>>; products: Awaited<ReturnType<typeof getProducts>>; warehouses: Awaited<ReturnType<typeof getWarehouses>>; onDone: () => Promise<void> }) {
  const [lotId, setLotId] = useState(""); const [ownerId, setOwnerId] = useState(""); const [quantity, setQuantity] = useState(""); const [validUntil, setValidUntil] = useState("");
  const available = balances.filter((item) => Number(item.available_quantity) > 0 && lots.find((lot) => lot.id === item.lot_id)?.status === "VERIFIED");
  const mutation = useMutation({ mutationFn: () => { const balance = balances.find((item) => item.lot_id === lotId); const lot = lots.find((item) => item.id === lotId); if (!balance || !lot) throw new Error("balance"); return issueCommodityRight({ lot_id: lotId, owner_member_id: ownerId, quantity, redeem_warehouse_id: lot.warehouse_id, valid_until: validUntil ? new Date(validUntil).toISOString() : null, expected_balance_version: balance.version }); }, onSuccess: async () => { setQuantity(""); await onDone(); } });
  return <section className="rights-command panel"><div className="panel-heading"><h2>Выпустить обеспеченное право</h2><BadgeCheck size={18} /></div><form className="rights-form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><label className="span-two">Партия<select value={lotId} onChange={(event) => setLotId(event.target.value)} required><option value="">Выберите</option>{available.map((balance) => { const lot = lots.find((item) => item.id === balance.lot_id); return <option value={balance.lot_id} key={balance.lot_id}>{lot?.lot_number} · {products.find((item) => item.id === lot?.product_id)?.name} · доступно {balance.available_quantity}</option>; })}</select></label><label>Получатель<select value={ownerId} onChange={(event) => setOwnerId(event.target.value)} required><option value="">Выберите</option>{members.map((item) => <option value={item.member_id} key={item.member_id}>{item.display_name} · {item.member_number}</option>)}</select></label><label>Количество<input inputMode="decimal" value={quantity} onChange={(event) => setQuantity(event.target.value)} required /></label><label>Действует до<input type="datetime-local" value={validUntil} onChange={(event) => setValidUntil(event.target.value)} /></label><label>Место выдачи<input value={warehouses.find((item) => item.id === lots.find((lot) => lot.id === lotId)?.warehouse_id)?.name ?? ""} readOnly /></label><button className="primary-button" disabled={mutation.isPending}><BadgeCheck size={17} />Выпустить</button></form>{mutation.isError ? <p className="form-error" role="alert">{errorText(mutation.error)}</p> : null}</section>;
}

function OperationsForm({ cooperativeId, rights, members, onDone }: { cooperativeId: string; rights: CommodityRight[]; members: Awaited<ReturnType<typeof getInventoryMembers>>; onDone: () => Promise<void> }) {
  const active = rights.filter((item) => ["ISSUED", "TRANSFERRED"].includes(item.status));
  const [rightId, setRightId] = useState(""); const [toMember, setToMember] = useState(""); const [file, setFile] = useState<File | null>(null);
  const selected = active.find((item) => item.id === rightId);
  const transfer = useMutation({ mutationFn: async () => { if (!selected || !file) throw new Error("selection"); const evidenceId = await uploadEvidence(cooperativeId, file, "RIGHT_TRANSFER_AUTHORIZATION"); return transferCommodityRight(selected, toMember, [evidenceId]); }, onSuccess: async () => { setFile(null); setRightId(""); await onDone(); } });
  const redeem = useMutation({ mutationFn: async () => { if (!selected) throw new Error("selection"); return requestRightRedemption(selected); }, onSuccess: async () => { setRightId(""); await onDone(); } });
  return <section className="rights-command panel"><div className="panel-heading"><h2>Передача и запрос выдачи</h2><ArrowRightLeft size={18} /></div><form className="rights-form" onSubmit={(event) => event.preventDefault()}><label className="span-two">Товарное право<select value={rightId} onChange={(event) => setRightId(event.target.value)} required><option value="">Выберите</option>{active.map((item) => <option value={item.id} key={item.id}>{shortId(item.id)} · {members.find((member) => member.member_id === item.owner_member_id)?.display_name} · {item.quantity}</option>)}</select></label><label>Новый владелец<select value={toMember} onChange={(event) => setToMember(event.target.value)}><option value="">Выберите</option>{members.filter((item) => item.member_id !== selected?.owner_member_id).map((item) => <option value={item.member_id} key={item.member_id}>{item.display_name}</option>)}</select></label><label className="file-field">Согласие владельца<input aria-label="Согласие владельца" type="file" accept="application/pdf,image/jpeg,image/png,image/webp,text/plain" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label><span className="rights-command-buttons"><button className="secondary-button" type="button" disabled={!selected || !toMember || !file || transfer.isPending} onClick={() => transfer.mutate()}><ArrowRightLeft size={16} />Передать</button><button className="primary-button" type="button" disabled={!selected || redeem.isPending} onClick={() => redeem.mutate()}><PackageCheck size={16} />Запросить выдачу</button></span></form>{transfer.isError || redeem.isError ? <p className="form-error" role="alert">{errorText(transfer.error ?? redeem.error)}</p> : null}</section>;
}

function FulfillmentForm({ cooperativeId, rights, redemptions, onDone }: { cooperativeId: string; rights: CommodityRight[]; redemptions: Awaited<ReturnType<typeof getRightRedemptions>>; onDone: () => Promise<void> }) {
  const pending = redemptions.filter((item) => item.status === "REQUESTED"); const [redemptionId, setRedemptionId] = useState(""); const [file, setFile] = useState<File | null>(null); const selected = pending.find((item) => item.id === redemptionId); const right = rights.find((item) => item.id === selected?.right_id);
  const mutation = useMutation({ mutationFn: async () => { if (!selected || !right || !file) throw new Error("selection"); const evidenceId = await uploadEvidence(cooperativeId, file, "RIGHT_REDEMPTION_ACT"); return completeRightRedemption(selected, right, [evidenceId]); }, onSuccess: async () => { setFile(null); setRedemptionId(""); await onDone(); } });
  return <section className="rights-command panel"><div className="panel-heading"><h2>Фактическая выдача товара</h2><PackageCheck size={18} /></div><form className="rights-form compact-rights-form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><label className="span-two">Запрос<select value={redemptionId} onChange={(event) => setRedemptionId(event.target.value)} required><option value="">Выберите</option>{pending.map((item) => <option value={item.id} key={item.id}>{shortId(item.right_id)} · {item.quantity} · {formatLocalDateTime(item.requested_at)}</option>)}</select></label><label className="file-field">Акт выдачи<input aria-label="Акт выдачи" type="file" accept="application/pdf,image/jpeg,image/png,image/webp,text/plain" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label><button className="primary-button" disabled={!right || !file || mutation.isPending}><PackageCheck size={17} />Подтвердить выдачу</button></form>{pending.length === 0 ? <p className="command-empty">Нет запросов на выдачу</p> : null}{mutation.isError ? <p className="form-error" role="alert">{errorText(mutation.error)}</p> : null}</section>;
}

function ControlForm({ rights, onDone }: { rights: CommodityRight[]; onDone: () => Promise<void> }) {
  const [rightId, setRightId] = useState(""); const [reason, setReason] = useState("PROTECTIVE_REVIEW"); const [decision, setDecision] = useState(""); const selected = rights.find((item) => item.id === rightId);
  const freeze = useMutation({ mutationFn: async () => { if (!selected) throw new Error("selection"); return freezeCommodityRight(selected, reason, decision); }, onSuccess: onDone });
  const unfreeze = useMutation({ mutationFn: async () => { if (!selected) throw new Error("selection"); return unfreezeCommodityRight(selected, decision); }, onSuccess: onDone });
  return <section className="rights-command panel"><div className="panel-heading"><h2>Защитная заморозка</h2><ShieldAlert size={18} /></div><form className="rights-form" onSubmit={(event: FormEvent) => event.preventDefault()}><label className="span-two">Товарное право<select value={rightId} onChange={(event) => setRightId(event.target.value)} required><option value="">Выберите</option>{rights.filter((item) => item.status !== "REDEEMED").map((item) => <option value={item.id} key={item.id}>{shortId(item.id)} · {statusNames[item.status]} · {item.quantity}</option>)}</select></label><label>Основание<input value={reason} onChange={(event) => setReason(event.target.value)} required /></label><label>Решение / дело<input value={decision} onChange={(event) => setDecision(event.target.value)} required /></label><span className="rights-command-buttons"><button className="secondary-button" type="button" disabled={!selected || selected.status === "FROZEN" || !decision || freeze.isPending} onClick={() => freeze.mutate()}><LockKeyhole size={16} />Заморозить</button><button className="primary-button" type="button" disabled={!selected || selected.status !== "FROZEN" || !decision || unfreeze.isPending} onClick={() => unfreeze.mutate()}><UnlockKeyhole size={16} />Разморозить</button></span></form>{freeze.isError || unfreeze.isError ? <p className="form-error" role="alert">{errorText(freeze.error ?? unfreeze.error)}</p> : null}</section>;
}
