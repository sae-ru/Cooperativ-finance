import {
  AlertTriangle,
  ArrowRightLeft,
  Boxes,
  CheckCircle2,
  ClipboardCheck,
  Download,
  FilePlus2,
  PackageCheck,
  PackagePlus,
  Printer,
  RefreshCw,
  Scale,
  ShieldCheck,
  Warehouse as WarehouseIcon,
  X,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useMemo, useState } from "react";

import { AdminApiError, type Principal, type RoleCode } from "./api/admin";
import {
  acceptCustody,
  attestLot,
  createProduct,
  createUnit,
  createWarehouse,
  downloadEvidence,
  getCustodyTransfers,
  getDiscrepancies,
  getInventoryCustodians,
  getInventoryMembers,
  getLots,
  getProducts,
  getReceiptAct,
  getUnits,
  getWarehouses,
  offerCustody,
  recordDiscrepancy,
  registerLot,
  uploadEvidence,
  type CustodyTransfer,
  type InventoryCustodian,
  type InventoryLot,
  type InventoryMember,
  type Product,
  type ReceiptAct,
  type UnitOfMeasure,
  type Warehouse,
} from "./api/inventory";
import { userErrorMessage } from "./shared/api-error";
import { formatLocalDateTime } from "./shared/date-time";
import "./inventory.css";

type Section = "lots" | "receive" | "control" | "custody" | "catalog";

const evidenceAccept = "application/pdf,image/jpeg,image/png,image/webp,text/plain";

const statusNames: Record<string, string> = {
  PENDING_VERIFICATION: "Ожидает контроля",
  VERIFIED: "Подтверждено",
  DISPUTED: "Расхождение",
  FROZEN: "Заморожено",
  LOST: "Утрачено",
  OFFERED: "Передача предложена",
  ACCEPTED: "Принято",
  OPEN: "Открыто",
  RESOLVED: "Закрыто",
};

function hasRole(principal: Principal, ...roles: RoleCode[]): boolean {
  return principal.roles.some((grant) => roles.includes(grant.role));
}

function errorText(error: unknown): string {
  return userErrorMessage(error);
}

function displayQuantity(value: string, scale: number): string {
  const [integer = "0", fraction = ""] = value.split(".");
  if (scale === 0) return integer;
  return `${integer}.${fraction.padEnd(scale, "0").slice(0, scale)}`;
}

function StatusPill({ value }: { value: string }) {
  const kind = ["VERIFIED", "ACCEPTED", "RESOLVED", "ACTIVE"].includes(value)
    ? "good"
    : ["DISPUTED", "FROZEN", "LOST", "REJECTED"].includes(value)
      ? "bad"
      : "warn";
  return <span className={`status ${kind}`}>{statusNames[value] ?? value}</span>;
}

function MutationError({ error }: { error: unknown }) {
  return error ? <p className="form-error" role="alert">{errorText(error)}</p> : null;
}

function ReceiptDialog({ act, onClose }: { act: ReceiptAct; onClose: () => void }) {
  const download = useMutation({
    mutationFn: async (evidenceId: string) => {
      const evidence = act.evidence.find((item) => item.id === evidenceId);
      const blob = await downloadEvidence(evidenceId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = evidence?.original_name ?? "evidence";
      anchor.click();
      URL.revokeObjectURL(url);
    },
  });
  return (
    <dialog className="receipt-dialog" open aria-labelledby="receipt-title">
      <article className="receipt-act">
        <header className="receipt-toolbar no-print">
          <strong>Акт приемки</strong>
          <span className="icon-actions">
            <button title="Печать" onClick={() => window.print()}><Printer size={17} /></button>
            <button title="Закрыть" onClick={onClose}><X size={17} /></button>
          </span>
        </header>
        <div className="receipt-body">
          <header className="receipt-title">
            <div><span>Cooperative Clearing</span><h1 id="receipt-title">Акт приемки партии</h1></div>
            <div><strong>{act.lot.lot_number}</strong><span>{formatLocalDateTime(act.generated_at)}</span></div>
          </header>
          <section className="receipt-grid">
            <div><span>Товар</span><strong>{act.product.name}</strong><small>{act.product.sku}</small></div>
            <div><span>Количество</span><strong>{displayQuantity(act.lot.current_quantity ?? act.lot.declared_quantity, act.unit.decimal_scale)} {act.unit.symbol}</strong><small>заявлено {displayQuantity(act.lot.declared_quantity, act.unit.decimal_scale)}</small></div>
            <div><span>Склад</span><strong>{act.warehouse.name}</strong><small>{act.warehouse.address_text}</small></div>
            <div><span>Статус</span><StatusPill value={act.lot.status} /></div>
          </section>
          <section className="receipt-parties">
            <div><span>Владелец</span><strong>{act.owner_name}</strong></div>
            <div><span>Принял</span><strong>{act.receiver_name}</strong></div>
            <div><span>Хранитель</span><strong>{act.custodian_name}</strong></div>
            <div><span>Независимый контроль</span><strong>{act.attester_name ?? "Не выполнен"}</strong></div>
          </section>
          {act.attestation ? (
            <section className="receipt-section">
              <h2>Результат контроля</h2>
              <dl>
                <div><dt>Измерено</dt><dd>{String(act.attestation.measured_quantity)} {act.unit.symbol}</dd></div>
                <div><dt>Отклонение</dt><dd>{String(act.attestation.variance)} {act.unit.symbol}</dd></div>
                <div><dt>Качество</dt><dd>{String(act.attestation.verified_quality)}</dd></div>
                <div><dt>Решение</dt><dd>{String(act.attestation.quality_decision)}</dd></div>
              </dl>
            </section>
          ) : null}
          <section className="receipt-section">
            <h2>Доказательства</h2>
            {act.evidence.length ? act.evidence.map((item) => (
              <div className="receipt-evidence" key={item.id}>
                <div><strong>{item.original_name}</strong><code>{item.expected_sha256}</code></div>
                <button className="icon-button no-print" title="Скачать доказательство" onClick={() => download.mutate(item.id)}><Download size={16} /></button>
              </div>
            )) : <span className="muted-value">Нет вложений</span>}
          </section>
          <section className="receipt-section">
            <h2>Подписанная цепочка событий</h2>
            <table><thead><tr><th>Версия</th><th>Событие</th><th>Время</th><th>Hash</th></tr></thead>
              <tbody>{act.signed_events.map((event) => <tr key={event.event_id}><td>{event.aggregate_version}</td><td>{event.event_type}</td><td>{formatLocalDateTime(event.occurred_at)}</td><td><code>{event.event_hash}</code></td></tr>)}</tbody>
            </table>
          </section>
          <footer className="receipt-signatures"><span>Получатель ____________________</span><span>Контролёр ____________________</span></footer>
        </div>
      </article>
    </dialog>
  );
}

function ReceiveForm({
  principal,
  products,
  units,
  warehouses,
  members,
  custodians,
  onDone,
}: {
  principal: Principal;
  products: Product[];
  units: UnitOfMeasure[];
  warehouses: Warehouse[];
  members: InventoryMember[];
  custodians: InventoryCustodian[];
  onDone: () => Promise<void>;
}) {
  const [lotNumber, setLotNumber] = useState("");
  const [productId, setProductId] = useState(products[0]?.id ?? "");
  const [warehouseId, setWarehouseId] = useState("");
  const [ownerId, setOwnerId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [quality, setQuality] = useState("Соответствует декларации владельца");
  const [expiresAt, setExpiresAt] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const product = products.find((item) => item.id === productId);
  const warehouse = warehouses.find((item) => item.id === warehouseId);
  const assignment = custodians.find(
    (item) => item.user_id === principal.user_id && item.warehouse_id === warehouseId,
  );
  const mutation = useMutation({
    mutationFn: async () => {
      if (!product || !warehouse || !assignment) {
        throw new AdminApiError("CUSTODY_ASSIGNMENT_REQUIRED", null, 400);
      }
      if (product.requires_evidence && !file) {
        throw new AdminApiError("EVIDENCE_REQUIRED", null, 400);
      }
      const evidenceIds = file
        ? [await uploadEvidence(product.cooperative_id, file, "RECEIPT")]
        : [];
      return registerLot({
        cooperative_id: product.cooperative_id,
        lot_number: lotNumber,
        product_id: product.id,
        warehouse_id: warehouse.id,
        owner_member_id: ownerId,
        declared_quantity: quantity,
        unit_id: product.default_unit_id,
        declared_quality: quality,
        expires_at: expiresAt ? new Date(expiresAt).toISOString() : null,
        storage_conditions: warehouse.storage_conditions,
        custodian_assignment_id: assignment.assignment_id,
        evidence_ids: evidenceIds,
      });
    },
    onSuccess: async () => {
      setLotNumber(""); setQuantity(""); setExpiresAt(""); setFile(null);
      await onDone();
    },
  });
  return (
    <section className="inventory-command">
      <div className="panel-heading"><h2>Зарегистрировать поступление</h2><PackagePlus size={17} /></div>
      <form className="inventory-form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}>
        <label>Номер партии<input value={lotNumber} onChange={(event) => setLotNumber(event.target.value)} required /></label>
        <label>Товар<select value={productId} onChange={(event) => setProductId(event.target.value)} required><option value="">Выберите</option>{products.map((item) => <option value={item.id} key={item.id}>{item.name} · {item.sku}</option>)}</select></label>
        <label>Склад<select value={warehouseId} onChange={(event) => setWarehouseId(event.target.value)} required><option value="">Выберите</option>{warehouses.filter((item) => custodians.some((value) => value.user_id === principal.user_id && value.warehouse_id === item.id)).map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
        <label>Владелец<select value={ownerId} onChange={(event) => setOwnerId(event.target.value)} required><option value="">Выберите</option>{members.filter((item) => !product || item.cooperative_id === product.cooperative_id).map((item) => <option value={item.member_id} key={item.member_id}>{item.display_name} · {item.member_number}</option>)}</select></label>
        <label>Количество<input inputMode="decimal" value={quantity} onChange={(event) => setQuantity(event.target.value)} required /></label>
        <label>Единица<input value={units.find((item) => item.id === product?.default_unit_id)?.symbol ?? ""} readOnly /></label>
        <label>Качество<input value={quality} onChange={(event) => setQuality(event.target.value)} required /></label>
        <label>Срок годности<input type="datetime-local" value={expiresAt} onChange={(event) => setExpiresAt(event.target.value)} required={product?.shelf_life_required} /></label>
        <label className="file-field">Доказательство<input type="file" accept={evidenceAccept} onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>
        <button className="primary-button" type="submit" disabled={mutation.isPending}><FilePlus2 size={17} /><span>{mutation.isPending ? "Сохранение" : "Принять"}</span></button>
      </form>
      <MutationError error={mutation.error} />
    </section>
  );
}

function ControlForm({ lots, products, onDone }: { lots: InventoryLot[]; products: Product[]; onDone: () => Promise<void> }) {
  const pending = lots.filter((lot) => lot.status === "PENDING_VERIFICATION");
  const [lotId, setLotId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [decision, setDecision] = useState<"ACCEPTED" | "REJECTED">("ACCEPTED");
  const [quality, setQuality] = useState("Качество подтверждено");
  const [temperature, setTemperature] = useState("");
  const [packaging, setPackaging] = useState("Целая");
  const [notes, setNotes] = useState("Независимый контроль выполнен");
  const [file, setFile] = useState<File | null>(null);
  const lot = pending.find((item) => item.id === lotId);
  const product = products.find((item) => item.id === lot?.product_id);
  const mutation = useMutation({
    mutationFn: async () => {
      if (!lot || !product) throw new AdminApiError("LOT_REQUIRED", null, 400);
      if (product.requires_evidence && !file) throw new AdminApiError("EVIDENCE_REQUIRED", null, 400);
      const evidenceIds = file ? [await uploadEvidence(lot.cooperative_id, file, "ATTESTATION")] : [];
      return attestLot(lot, {
        measured_quantity: quantity,
        quality_decision: decision,
        verified_quality: quality,
        measurements: { temperature, packaging },
        notes,
        evidence_ids: evidenceIds,
      });
    },
    onSuccess: async () => { setLotId(""); setQuantity(""); setFile(null); await onDone(); },
  });
  return (
    <section className="inventory-command">
      <div className="panel-heading"><h2>Независимый контроль</h2><Scale size={17} /></div>
      <form className="inventory-form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}>
        <label className="span-two">Партия<select value={lotId} onChange={(event) => { setLotId(event.target.value); setQuantity(pending.find((item) => item.id === event.target.value)?.declared_quantity ?? ""); }} required><option value="">Выберите</option>{pending.map((item) => <option value={item.id} key={item.id}>{item.lot_number} · {products.find((value) => value.id === item.product_id)?.name}</option>)}</select></label>
        <label>Измерено<input inputMode="decimal" value={quantity} onChange={(event) => setQuantity(event.target.value)} required /></label>
        <label>Решение<select value={decision} onChange={(event) => setDecision(event.target.value as "ACCEPTED" | "REJECTED")}><option value="ACCEPTED">Принять качество</option><option value="REJECTED">Отклонить качество</option></select></label>
        <label>Подтвержденное качество<input value={quality} onChange={(event) => setQuality(event.target.value)} required /></label>
        <label>Температура<input value={temperature} onChange={(event) => setTemperature(event.target.value)} required /></label>
        <label>Упаковка<input value={packaging} onChange={(event) => setPackaging(event.target.value)} required /></label>
        <label>Примечание<input value={notes} onChange={(event) => setNotes(event.target.value)} required /></label>
        <label className="file-field">Доказательство<input type="file" accept={evidenceAccept} onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>
        <button className="primary-button" type="submit" disabled={mutation.isPending}><ClipboardCheck size={17} /><span>{mutation.isPending ? "Подписание" : "Подтвердить"}</span></button>
      </form>
      <MutationError error={mutation.error} />
    </section>
  );
}

function CustodyView({ principal, lots, warehouses, custodians, transfers, onDone }: { principal: Principal; lots: InventoryLot[]; warehouses: Warehouse[]; custodians: InventoryCustodian[]; transfers: CustodyTransfer[]; onDone: () => Promise<void> }) {
  const [offerLotId, setOfferLotId] = useState("");
  const [targetAssignmentId, setTargetAssignmentId] = useState("");
  const [place, setPlace] = useState("Складская зона передачи");
  const [offerNotes, setOfferNotes] = useState("Состояние партии зафиксировано");
  const [offerFile, setOfferFile] = useState<File | null>(null);
  const offerLot = lots.find((item) => item.id === offerLotId);
  const targets = custodians.filter((item) => item.assignment_id !== offerLot?.custodian_assignment_id);
  const target = targets.find((item) => item.assignment_id === targetAssignmentId);
  const offer = useMutation({
    mutationFn: async () => {
      if (!offerLot || !target) throw new AdminApiError("CUSTODY_TARGET_REQUIRED", null, 400);
      const evidenceIds = offerFile ? [await uploadEvidence(offerLot.cooperative_id, offerFile, "CUSTODY_OFFER")] : [];
      return offerCustody(offerLot, { to_warehouse_id: target.warehouse_id, to_assignment_id: target.assignment_id, place, notes: offerNotes, evidence_ids: evidenceIds });
    },
    onSuccess: async () => { setOfferLotId(""); setTargetAssignmentId(""); setOfferFile(null); await onDone(); },
  });

  const [transferId, setTransferId] = useState("");
  const [acceptFile, setAcceptFile] = useState<File | null>(null);
  const transfer = transfers.find((item) => item.id === transferId);
  const transferLot = lots.find((item) => item.id === transfer?.lot_id);
  const incoming = transfers.filter((item) => item.status === "OFFERED" && custodians.some((value) => value.assignment_id === item.to_assignment_id && value.user_id === principal.user_id));
  const accept = useMutation({
    mutationFn: async () => {
      if (!transfer || !transferLot || !acceptFile) throw new AdminApiError("ACCEPTANCE_EVIDENCE_REQUIRED", null, 400);
      const evidenceId = await uploadEvidence(transferLot.cooperative_id, acceptFile, "CUSTODY_ACCEPTANCE");
      return acceptCustody(transfer, transferLot, [evidenceId]);
    },
    onSuccess: async () => { setTransferId(""); setAcceptFile(null); await onDone(); },
  });

  const [discrepancyLotId, setDiscrepancyLotId] = useState("");
  const [actual, setActual] = useState("");
  const [reason, setReason] = useState("PHYSICAL_COUNT");
  const [discrepancyNotes, setDiscrepancyNotes] = useState("Расхождение обнаружено при пересчете");
  const [discrepancyFile, setDiscrepancyFile] = useState<File | null>(null);
  const discrepancyLot = lots.find((item) => item.id === discrepancyLotId);
  const discrepancy = useMutation({
    mutationFn: async () => {
      if (!discrepancyLot || !discrepancyFile) throw new AdminApiError("DISCREPANCY_EVIDENCE_REQUIRED", null, 400);
      const evidenceId = await uploadEvidence(discrepancyLot.cooperative_id, discrepancyFile, "DISCREPANCY");
      return recordDiscrepancy(discrepancyLot, { actual_quantity: actual, reason_code: reason, notes: discrepancyNotes, evidence_ids: [evidenceId] });
    },
    onSuccess: async () => { setDiscrepancyLotId(""); setActual(""); setDiscrepancyFile(null); await onDone(); },
  });

  const ownLots = lots.filter((lot) => custodians.some((item) => item.assignment_id === lot.custodian_assignment_id && item.user_id === principal.user_id));
  return <div className="view-stack inner-stack">
    {hasRole(principal, "WAREHOUSE_CUSTODIAN", "LOGISTICS_OPERATOR") ? <section className="inventory-command"><div className="panel-heading"><h2>Предложить передачу хранения</h2><ArrowRightLeft size={17} /></div><form className="inventory-form" onSubmit={(event) => { event.preventDefault(); offer.mutate(); }}>
      <label>Партия<select value={offerLotId} onChange={(event) => { setOfferLotId(event.target.value); setTargetAssignmentId(""); }} required><option value="">Выберите</option>{ownLots.map((item) => <option value={item.id} key={item.id}>{item.lot_number}</option>)}</select></label>
      <label>Новый хранитель<select value={targetAssignmentId} onChange={(event) => setTargetAssignmentId(event.target.value)} required><option value="">Выберите</option>{targets.map((item) => <option value={item.assignment_id} key={item.assignment_id}>{item.display_name} · {warehouses.find((value) => value.id === item.warehouse_id)?.name}</option>)}</select></label>
      <label>Место передачи<input value={place} onChange={(event) => setPlace(event.target.value)} required /></label><label>Состояние<input value={offerNotes} onChange={(event) => setOfferNotes(event.target.value)} required /></label>
      <label className="file-field">Фото или акт<input type="file" accept={evidenceAccept} onChange={(event) => setOfferFile(event.target.files?.[0] ?? null)} /></label><button className="primary-button" type="submit" disabled={offer.isPending}><ArrowRightLeft size={17} /><span>Предложить</span></button>
    </form><MutationError error={offer.error} /></section> : null}
    {hasRole(principal, "WAREHOUSE_CUSTODIAN") ? <section className="inventory-command"><div className="panel-heading"><h2>Принять хранение</h2><PackageCheck size={17} /></div><form className="inventory-form compact-form" onSubmit={(event) => { event.preventDefault(); accept.mutate(); }}>
      <label className="span-two">Предложение<select value={transferId} onChange={(event) => setTransferId(event.target.value)} required><option value="">Выберите</option>{incoming.map((item) => <option value={item.id} key={item.id}>{lots.find((lot) => lot.id === item.lot_id)?.lot_number} · {item.place}</option>)}</select></label>
      <label className="file-field">Акт получения<input type="file" accept={evidenceAccept} onChange={(event) => setAcceptFile(event.target.files?.[0] ?? null)} /></label><button className="primary-button" type="submit" disabled={accept.isPending}><CheckCircle2 size={17} /><span>Принять</span></button>
    </form><MutationError error={accept.error} /></section> : null}
    <section className="inventory-command"><div className="panel-heading"><h2>Зафиксировать расхождение</h2><AlertTriangle size={17} /></div><form className="inventory-form" onSubmit={(event) => { event.preventDefault(); discrepancy.mutate(); }}>
      <label>Партия<select value={discrepancyLotId} onChange={(event) => { setDiscrepancyLotId(event.target.value); setActual(lots.find((item) => item.id === event.target.value)?.current_quantity ?? ""); }} required><option value="">Выберите</option>{lots.map((item) => <option value={item.id} key={item.id}>{item.lot_number}</option>)}</select></label>
      <label>Фактически<input inputMode="decimal" value={actual} onChange={(event) => setActual(event.target.value)} required /></label><label>Причина<input value={reason} onChange={(event) => setReason(event.target.value)} required /></label><label>Описание<input value={discrepancyNotes} onChange={(event) => setDiscrepancyNotes(event.target.value)} required /></label>
      <label className="file-field">Доказательство<input type="file" accept={evidenceAccept} onChange={(event) => setDiscrepancyFile(event.target.files?.[0] ?? null)} /></label><button className="primary-button" type="submit" disabled={discrepancy.isPending}><AlertTriangle size={17} /><span>Зафиксировать</span></button>
    </form><MutationError error={discrepancy.error} /></section>
  </div>;
}

function CatalogView({ cooperativeId, units, onDone }: { cooperativeId: string; units: UnitOfMeasure[]; onDone: () => Promise<void> }) {
  const [unitCode, setUnitCode] = useState(""); const [unitName, setUnitName] = useState(""); const [unitSymbol, setUnitSymbol] = useState(""); const [unitScale, setUnitScale] = useState(3);
  const unit = useMutation({ mutationFn: () => createUnit({ cooperative_id: cooperativeId, code: unitCode, name: unitName, symbol: unitSymbol, dimension: "MASS", decimal_scale: unitScale }), onSuccess: onDone });
  const [sku, setSku] = useState(""); const [productName, setProductName] = useState(""); const [unitId, setUnitId] = useState(""); const [tolerance, setTolerance] = useState("0");
  const product = useMutation({ mutationFn: () => createProduct({ cooperative_id: cooperativeId, sku, name: productName, description: productName, default_unit_id: unitId, quantity_tolerance: tolerance, requires_evidence: true, shelf_life_required: false }), onSuccess: onDone });
  const [warehouseCode, setWarehouseCode] = useState(""); const [warehouseName, setWarehouseName] = useState(""); const [address, setAddress] = useState(""); const [conditions, setConditions] = useState("");
  const warehouse = useMutation({ mutationFn: () => createWarehouse({ cooperative_id: cooperativeId, code: warehouseCode, name: warehouseName, address_text: address, storage_conditions: conditions }), onSuccess: onDone });
  if (!cooperativeId) return <div className="state error"><AlertTriangle size={22} /><strong>Для роли не задан кооператив</strong></div>;
  return <div className="view-stack inner-stack">
    <section className="inventory-command"><div className="panel-heading"><h2>Единица измерения</h2><Scale size={17} /></div><form className="inventory-form compact-form" onSubmit={(event) => { event.preventDefault(); unit.mutate(); }}><label>Код<input value={unitCode} onChange={(event) => setUnitCode(event.target.value)} required /></label><label>Наименование<input value={unitName} onChange={(event) => setUnitName(event.target.value)} required /></label><label>Обозначение<input value={unitSymbol} onChange={(event) => setUnitSymbol(event.target.value)} required /></label><label>Знаков после запятой<input type="number" min="0" max="12" value={unitScale} onChange={(event) => setUnitScale(Number(event.target.value))} required /></label><button className="primary-button" type="submit"><PackagePlus size={17} /><span>Создать</span></button></form><MutationError error={unit.error} /></section>
    <section className="inventory-command"><div className="panel-heading"><h2>Товар</h2><Boxes size={17} /></div><form className="inventory-form compact-form" onSubmit={(event) => { event.preventDefault(); product.mutate(); }}><label>SKU<input value={sku} onChange={(event) => setSku(event.target.value)} required /></label><label>Наименование<input value={productName} onChange={(event) => setProductName(event.target.value)} required /></label><label>Единица<select value={unitId} onChange={(event) => setUnitId(event.target.value)} required><option value="">Выберите</option>{units.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label><label>Допуск<input inputMode="decimal" value={tolerance} onChange={(event) => setTolerance(event.target.value)} required /></label><button className="primary-button" type="submit"><PackagePlus size={17} /><span>Создать</span></button></form><MutationError error={product.error} /></section>
    <section className="inventory-command"><div className="panel-heading"><h2>Склад</h2><WarehouseIcon size={17} /></div><form className="inventory-form compact-form" onSubmit={(event) => { event.preventDefault(); warehouse.mutate(); }}><label>Код<input value={warehouseCode} onChange={(event) => setWarehouseCode(event.target.value)} required /></label><label>Наименование<input value={warehouseName} onChange={(event) => setWarehouseName(event.target.value)} required /></label><label>Адрес<input value={address} onChange={(event) => setAddress(event.target.value)} required /></label><label>Условия хранения<input value={conditions} onChange={(event) => setConditions(event.target.value)} required /></label><button className="primary-button" type="submit"><PackagePlus size={17} /><span>Создать</span></button></form><MutationError error={warehouse.error} /></section>
  </div>;
}

export default function InventoryView({ principal }: { principal: Principal }) {
  const client = useQueryClient();
  const units = useQuery({ queryKey: ["inventory", "units"], queryFn: getUnits });
  const products = useQuery({ queryKey: ["inventory", "products"], queryFn: getProducts });
  const warehouses = useQuery({ queryKey: ["inventory", "warehouses"], queryFn: getWarehouses });
  const members = useQuery({ queryKey: ["inventory", "members"], queryFn: getInventoryMembers });
  const custodians = useQuery({ queryKey: ["inventory", "custodians"], queryFn: getInventoryCustodians });
  const lots = useQuery({ queryKey: ["inventory", "lots"], queryFn: getLots });
  const discrepancies = useQuery({ queryKey: ["inventory", "discrepancies"], queryFn: getDiscrepancies });
  const transfers = useQuery({ queryKey: ["inventory", "transfers"], queryFn: getCustodyTransfers });
  const queries = [units, products, warehouses, members, custodians, lots, discrepancies, transfers];
  const refresh = () => client.invalidateQueries({ queryKey: ["inventory"] });
  const available = useMemo(() => {
    const result: Section[] = ["lots"];
    if (hasRole(principal, "WAREHOUSE_CUSTODIAN")) result.push("receive");
    if (hasRole(principal, "INVENTORY_CONTROLLER", "AUDITOR")) result.push("control");
    if (hasRole(principal, "WAREHOUSE_CUSTODIAN", "LOGISTICS_OPERATOR", "INVENTORY_CONTROLLER", "AUDITOR", "RISK_ADMIN", "SECURITY_ADMIN")) result.push("custody");
    if (hasRole(principal, "DATA_STEWARD", "COOPERATIVE_ADMIN")) result.push("catalog");
    return result;
  }, [principal]);
  const [section, setSection] = useState<Section>(available[0] ?? "lots");
  const [act, setAct] = useState<ReceiptAct | null>(null);
  const printAct = useMutation({ mutationFn: getReceiptAct, onSuccess: setAct });
  const nav = [["lots", "Партии", Boxes], ["receive", "Приемка", PackagePlus], ["control", "Контроль", ClipboardCheck], ["custody", "Хранение", ArrowRightLeft], ["catalog", "Справочники", WarehouseIcon]] as const;
  if (queries.some((query) => query.isPending)) return <div className="state"><RefreshCw className="spin" size={24} /><span>Загрузка склада</span></div>;
  const failed = queries.find((query) => query.isError);
  if (failed) return <div className="state error"><AlertTriangle size={24} /><strong>{errorText(failed.error)}</strong></div>;
  const lotData = lots.data ?? []; const productData = products.data ?? []; const warehouseData = warehouses.data ?? []; const custodianData = custodians.data ?? [];
  const cooperativeId = principal.roles.find((grant) => grant.cooperative_id)?.cooperative_id ?? productData[0]?.cooperative_id ?? "";
  return <div className="view-stack inventory-view">
    <header className="view-header"><div><span className="eyebrow">Физический контур</span><h1>Склад и ответственность</h1><p>Приемка, независимый контроль и непрерывная цепочка хранения</p></div><div className="section-tabs">{nav.filter(([key]) => available.includes(key)).map(([key, label, Icon]) => <button className={section === key ? "active" : ""} onClick={() => setSection(key)} key={key}><Icon size={16} /><span>{label}</span></button>)}</div></header>
    <section className="metric-grid responsibility-metrics" aria-label="Состояние запасов"><article className="metric"><Boxes size={18} /><span>Партии</span><strong>{lotData.length}</strong></article><article className="metric"><ClipboardCheck size={18} /><span>Ждут контроля</span><strong>{lotData.filter((item) => item.status === "PENDING_VERIFICATION").length}</strong></article><article className="metric"><AlertTriangle size={18} /><span>Расхождения</span><strong>{discrepancies.data?.filter((item) => item.status === "OPEN").length ?? 0}</strong></article><article className="metric"><ArrowRightLeft size={18} /><span>В передаче</span><strong>{transfers.data?.filter((item) => item.status === "OFFERED").length ?? 0}</strong></article></section>
    {section === "lots" ? <section className="panel"><div className="panel-heading"><h2>Реестр партий</h2><span>{lotData.length}</span></div><div className="table-wrap"><table className="inventory-table"><thead><tr><th>Статус</th><th>Партия и товар</th><th>Склад</th><th>Количество</th><th>Ответственный</th><th>Акт</th></tr></thead><tbody>{lotData.map((lot) => <tr key={lot.id}><td><StatusPill value={lot.status} /><small>{formatLocalDateTime(lot.updated_at)}</small></td><td><strong>{lot.lot_number}</strong><small>{productData.find((item) => item.id === lot.product_id)?.name ?? lot.product_id}</small></td><td><strong>{warehouseData.find((item) => item.id === lot.warehouse_id)?.name ?? lot.warehouse_id}</strong><small>{lot.storage_conditions}</small></td><td><strong>{displayQuantity(lot.current_quantity ?? lot.declared_quantity, units.data?.find((item) => item.id === lot.unit_id)?.decimal_scale ?? 12)}</strong><small>заявлено {displayQuantity(lot.declared_quantity, units.data?.find((item) => item.id === lot.unit_id)?.decimal_scale ?? 12)}</small></td><td><strong>{custodianData.find((item) => item.assignment_id === lot.custodian_assignment_id)?.display_name ?? "—"}</strong><small>{lot.custodian_assignment_id}</small></td><td><button className="icon-button" title="Открыть акт" onClick={() => printAct.mutate(lot.id)}><Printer size={16} /></button></td></tr>)}</tbody></table></div></section> : null}
    {section === "receive" ? <ReceiveForm principal={principal} products={productData} units={units.data ?? []} warehouses={warehouseData} members={members.data ?? []} custodians={custodianData} onDone={refresh} /> : null}
    {section === "control" ? <ControlForm lots={lotData} products={productData} onDone={refresh} /> : null}
    {section === "custody" ? <CustodyView principal={principal} lots={lotData} warehouses={warehouseData} custodians={custodianData} transfers={transfers.data ?? []} onDone={refresh} /> : null}
    {section === "catalog" ? <CatalogView cooperativeId={cooperativeId} units={units.data ?? []} onDone={refresh} /> : null}
    {printAct.isError ? <div className="state error"><AlertTriangle size={22} /><strong>{errorText(printAct.error)}</strong></div> : null}
    {act ? <ReceiptDialog act={act} onClose={() => setAct(null)} /> : null}
  </div>;
}
