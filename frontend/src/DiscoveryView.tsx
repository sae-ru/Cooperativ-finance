import {
  Archive,
  ChevronDown,
  CircleCheck,
  Database,
  ListChecks,
  PackageCheck,
  RadioTower,
  RefreshCw,
  Search,
  ShieldCheck,
  ShoppingCart,
  Store,
  Truck,
  XCircle,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { AdminApiError, type Principal } from "./api/admin";
import {
  cancelPurchase,
  commitPurchase,
  createPurchaseIntent,
  getPurchaseIntents,
  getReservationReceipts,
  publishOffer,
  reservePurchase,
  searchCatalog,
  verifyOffer,
  type OfferDraft,
  type PurchaseIntent,
  type SearchCandidate,
  type SearchFilters,
  type SearchMode,
} from "./api/discovery";
import "./i18n";
import { formatLocalDateTime } from "./shared/date-time";
import "./discovery.css";

type Section = "search" | "sell" | "intents";
type ProductPreset = { code: string; key: string; unit: string; visual: string; aliases: string[] };

const productPresets: ProductPreset[] = [
  { code: "CABBAGE.WHITE", key: "market.cabbage", unit: "KG", visual: "cabbage", aliases: ["капуста", "cabbage"] },
  { code: "NAIL.STEEL.100MM", key: "market.nails", unit: "PCS", visual: "nails", aliases: ["гвозди", "гвоздь", "nails", "nail"] },
  { code: "MILK.UHT.3_2", key: "market.milk", unit: "L", visual: "milk", aliases: ["молоко", "milk"] },
];

const initialFilters: SearchFilters = {
  mode: "DIRECT",
  product_code: "CABBAGE.WHITE",
  quantity: "10.000",
  unit_code: "KG",
  valuation_unit: "COOP",
  destination_region: "EAST-DISTRICT",
  maximum_age_seconds: 604800,
  trusted_node_codes: [],
  required_certificates: [],
  quality_minimum: "A",
  maximum_goods_cost: null,
  maximum_landed_cost: null,
  latest_delivery: null,
  top_k: 20,
};

function errorText(error: unknown): string {
  if (error instanceof AdminApiError) {
    return `${error.code}${error.requestId ? ` · ${error.requestId}` : ""}`;
  }
  if (error instanceof Error) return error.message;
  return "OPERATION_FAILED";
}

function formatAmount(value: string | null, unit?: string, sharesUnit?: string): string {
  if (value === null) return "—";
  const number = Number(value);
  const locale = document.documentElement.lang.startsWith("en") ? "en-US" : "ru-RU";
  const formatted = Number.isFinite(number)
    ? new Intl.NumberFormat(locale, { maximumFractionDigits: 4 }).format(number)
    : value;
  const displayUnit = unit === "COOP" && sharesUnit ? sharesUnit : unit;
  return displayUnit ? `${formatted} ${displayUnit}` : formatted;
}

function resolveProduct(value: string): ProductPreset | null {
  const normalized = value.trim().toLowerCase();
  return productPresets.find((preset) =>
    preset.code.toLowerCase() === normalized || preset.aliases.some((alias) => normalized.includes(alias)),
  ) ?? null;
}

function visualFor(productCode: string): string {
  return productPresets.find((preset) => preset.code === productCode)?.visual ?? "crate";
}

function StatusBadge({ value, label }: { value: string; label: string }) {
  const kind = ["LIVE_VERIFIED", "COMMITTED", "PREPARED", "CONFIRMED", "ACTIVE"].includes(value)
    ? "good"
    : ["REVOKED_OR_UNTRUSTED", "EXPIRED"].includes(value)
      ? "bad"
      : "warn";
  return <span className={`status ${kind}`}>{label}</span>;
}

export default function DiscoveryView({ principal }: { principal: Principal }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [section, setSection] = useState<Section>("search");
  const [filters, setFilters] = useState<SearchFilters>(initialFilters);
  const [productQuery, setProductQuery] = useState(t("market.cabbage"));
  const [submitted, setSubmitted] = useState<SearchFilters | null>(initialFilters);
  const [selectedIntentId, setSelectedIntentId] = useState("");

  const results = useQuery({
    queryKey: ["federated-search", submitted],
    queryFn: () => searchCatalog(submitted ?? initialFilters),
    enabled: submitted !== null,
  });
  const intents = useQuery({ queryKey: ["purchase-intents"], queryFn: getPurchaseIntents });
  const receipts = useQuery({
    queryKey: ["purchase-receipts", selectedIntentId],
    queryFn: () => getReservationReceipts(selectedIntentId),
    enabled: Boolean(selectedIntentId),
  });

  const refreshIntents = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["purchase-intents"] }),
      queryClient.invalidateQueries({ queryKey: ["purchase-receipts"] }),
    ]);
  };
  const prepare = useMutation({
    mutationFn: (candidate: SearchCandidate) =>
      createPurchaseIntent(candidate, submitted?.quantity ?? filters.quantity),
    onSuccess: async (result) => {
      setSelectedIntentId(result.object_id);
      setSection("intents");
      await refreshIntents();
    },
  });
  const reserve = useMutation({
    mutationFn: ({ intentId, kind }: { intentId: string; kind: "goods" | "logistics" }) =>
      reservePurchase(intentId, kind),
    onSuccess: refreshIntents,
  });
  const commit = useMutation({ mutationFn: commitPurchase, onSuccess: refreshIntents });
  const cancel = useMutation({
    mutationFn: (intent: PurchaseIntent) => cancelPurchase(intent, "Отмена участником обмена"),
    onSuccess: refreshIntents,
  });
  const verify = useMutation({
    mutationFn: verifyOffer,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["federated-search"] }),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    const preset = resolveProduct(productQuery);
    const next = {
      ...filters,
      product_code: preset?.code ?? productQuery.trim().toUpperCase(),
      unit_code: preset?.unit ?? filters.unit_code,
    };
    setFilters(next);
    setSubmitted(next);
  }

  function chooseProduct(preset: ProductPreset) {
    const next = { ...filters, product_code: preset.code, unit_code: preset.unit };
    setProductQuery(t(preset.key));
    setFilters(next);
    setSubmitted(next);
  }

  const candidates = results.data?.data ?? [];
  const peerStatuses = results.data?.peer_statuses ?? [];
  const intentRows = intents.data ?? [];
  const activeIntents = intentRows.filter((item) =>
    ["PREPARING", "GOODS_RESERVED", "PREPARED", "COMMITTING", "CANCELLING"].includes(item.status),
  ).length;
  const combinedError = results.error ?? prepare.error ?? verify.error ?? reserve.error ?? commit.error ?? cancel.error;

  return (
    <div className="view-stack discovery-view">
      <header className="market-header">
        <div>
          <span className="eyebrow">{t("market.eyebrow")}</span>
          <h1>{t("market.title")}</h1>
          <p>{t("market.subtitle")}</p>
        </div>
        <div className="section-tabs market-tabs" role="tablist" aria-label={t("nav.market")}>
          <button className={section === "search" ? "active" : ""} onClick={() => setSection("search")} role="tab" aria-selected={section === "search"}>
            <ShoppingCart size={16} /><span>{t("market.catalogTab")}</span>
          </button><button className={section === "sell" ? "active" : ""} onClick={() => setSection("sell")} role="tab" aria-selected={section === "sell"}>
            <Store size={16} /><span>{t("market.sellTab")}</span>
          </button>
          <button className={section === "intents" ? "active" : ""} onClick={() => setSection("intents")} role="tab" aria-selected={section === "intents"}>
            <ListChecks size={16} /><span>{t("market.ordersTab")}</span>{activeIntents ? <b>{activeIntents}</b> : null}
          </button>
        </div>
      </header>

      {combinedError ? <p className="form-error discovery-error" role="alert">{errorText(combinedError)}</p> : null}

      {section === "search" ? (
        <>
          <section className="market-search" aria-label={t("market.find")}>
            <form onSubmit={submit}>
              <label className="product-query">
                <span>{t("market.searchLabel")}</span>
                <div className="search-input-wrap"><Search size={20} /><input value={productQuery} onChange={(event) => setProductQuery(event.target.value)} placeholder={t("market.searchPlaceholder")} required /></div>
              </label>
              <label><span>{t("market.quantity")}</span><input inputMode="decimal" value={filters.quantity} onChange={(event) => setFilters((value) => ({ ...value, quantity: event.target.value }))} required /></label>
              <label><span>{t("market.unit")}</span><select value={filters.unit_code} onChange={(event) => setFilters((value) => ({ ...value, unit_code: event.target.value }))}><option value="KG">kg</option><option value="L">L</option><option value="PCS">pcs</option></select></label>
              <label className="destination-query"><span>{t("market.destination")}</span><input value={filters.destination_region} onChange={(event) => setFilters((value) => ({ ...value, destination_region: event.target.value }))} placeholder={t("market.destinationPlaceholder")} required /></label>
              <button className="market-search-button" type="submit" disabled={results.isFetching}>{results.isFetching ? <RefreshCw className="spin" size={19} /> : <Search size={19} />}<span>{t("market.find")}</span></button>
            </form>
            <div className="popular-products"><span>{t("market.popular")}</span>{productPresets.map((preset) => <button type="button" key={preset.code} onClick={() => chooseProduct(preset)}><span className={`tiny-product ${preset.visual}`} />{t(preset.key)}</button>)}</div>
            <details className="advanced-search">
              <summary><ChevronDown size={17} />{t("market.moreFilters")}</summary>
              <div className="advanced-search-grid">
                <fieldset className="mode-control">
                  <legend>{t("market.source")}</legend>
                  {(["DIRECT", "INDEXED", "CACHED_OFFLINE"] as SearchMode[]).map((mode) => <label key={mode}><input type="radio" name="search-mode" checked={filters.mode === mode} onChange={() => setFilters((value) => ({ ...value, mode }))} />{mode === "DIRECT" ? <RadioTower size={15} /> : mode === "INDEXED" ? <Database size={15} /> : <Archive size={15} />}<span>{t(`market.mode.${mode}`)}</span></label>)}
                </fieldset>
                <label><span>{t("market.quality")}</span><input value={filters.quality_minimum ?? ""} onChange={(event) => setFilters((value) => ({ ...value, quality_minimum: event.target.value || null }))} /></label>
                <label><span>{t("market.maximumPrice")}</span><input inputMode="decimal" value={filters.maximum_landed_cost ?? ""} onChange={(event) => setFilters((value) => ({ ...value, maximum_landed_cost: event.target.value || null }))} /></label>
                <label><span>{t("market.deliveryBy")}</span><input type="date" value={filters.latest_delivery?.slice(0, 10) ?? ""} onChange={(event) => setFilters((value) => ({ ...value, latest_delivery: event.target.value ? new Date(`${event.target.value}T23:59:59Z`).toISOString() : null }))} /></label>
              </div>
            </details>
          </section>

          <section className="metric-grid discovery-metrics" aria-label={t("market.offers")}>
            <article className="metric"><Search size={19} /><span>{t("market.found")}</span><strong>{candidates.length}</strong></article>
            <article className="metric"><ShieldCheck size={19} /><span>{t("market.verified")}</span><strong>{candidates.filter((item) => item.signature_verified).length}</strong></article>
            <article className="metric"><Truck size={19} /><span>{t("market.withDelivery")}</span><strong>{candidates.filter((item) => item.quote).length}</strong></article>
            <article className="metric"><ListChecks size={19} /><span>{t("market.activeOrders")}</span><strong>{activeIntents}</strong></article>
          </section>

          {peerStatuses.length ? <div className="peer-status-list" aria-label={t("market.source")}>{peerStatuses.map((peer) => <span className={`peer-status ${peer.status.toLowerCase()}`} key={peer.node_code}><RadioTower size={14} />{peer.node_code}: {peer.status === "SUCCEEDED" ? `${peer.imported_offers} ${t("market.peerReceived")}` : peer.result_code}</span>)}</div> : null}

          <section className="market-results" aria-labelledby="offers-title">
            <div className="results-heading"><div><h2 id="offers-title">{t("market.offers")}</h2><p>{principal.login}</p></div><span>{candidates.length}</span></div>
            {results.isPending ? <div className="state"><RefreshCw className="spin" size={22} />{t("market.searching")}</div> : null}
            {!results.isPending && candidates.length === 0 ? <div className="empty-market"><Search size={28} /><strong>{t("market.noOffers")}</strong></div> : null}
            {candidates.length ? <div className="product-grid">{candidates.map((candidate) => <ProductCard candidate={candidate} key={`${candidate.offer.record_id}:${candidate.quote?.record_id ?? "none"}`} busy={prepare.isPending || verify.isPending} onBuy={() => prepare.mutate(candidate)} onVerify={() => verify.mutate(candidate.offer.record_id)} />)}</div> : null}
          </section>
        </>
      ) : section === "sell" ? (
        <SellPanel
          principal={principal}
          onViewMarket={(productCode, unitCode) => {
            const preset = productPresets.find((item) => item.code === productCode);
            const next = { ...filters, product_code: productCode, unit_code: unitCode };
            setProductQuery(preset ? t(preset.key) : productCode);
            setFilters(next);
            setSubmitted(next);
            setSection("search");
          }}
        />
      ) : (
        <OrderPanel intents={intentRows} receipts={receipts.data ?? []} selectedId={selectedIntentId} busy={reserve.isPending || commit.isPending || cancel.isPending} onSelect={setSelectedIntentId} onReserve={(intent, kind) => reserve.mutate({ intentId: intent.id, kind })} onCommit={(intent) => commit.mutate(intent)} onCancel={(intent) => cancel.mutate(intent)} />
      )}
    </div>
  );
}

function SellPanel({ principal, onViewMarket }: { principal: Principal; onViewMarket: (productCode: string, unitCode: string) => void }) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<OfferDraft>(() => ({
    product_code: "MILK.UHT.3_2",
    description: t("market.milk"),
    quantity_available: "100.000",
    unit_code: "L",
    minimum_batch: "10.000",
    origin_region: "EAST-DISTRICT",
    unit_price: "3.00",
    available_until: new Date(Date.now() + 7 * 24 * 60 * 60_000).toISOString().slice(0, 10),
  }));
  const canPublish = principal.roles.some((grant) => grant.role === "NODE_BUSINESS_OPERATOR");
  const mutation = useMutation({
    mutationFn: () => publishOffer(draft, principal.member_id ?? principal.login),
  });

  function selectProduct(productCode: string) {
    const preset = productPresets.find((item) => item.code === productCode) ?? productPresets[0];
    if (!preset) return;
    setDraft((value) => ({
      ...value,
      product_code: preset.code,
      unit_code: preset.unit,
      description: t(preset.key),
      minimum_batch: preset.unit === "PCS" ? "100" : "10.000",
    }));
    mutation.reset();
  }

  return (
    <section className="sell-layout" aria-labelledby="sell-title">
      <div className="sell-product-visual">
        <div className={`product-photo ${visualFor(draft.product_code)}`} role="img" aria-label={draft.description} />
        <div><strong>{t(productPresets.find((item) => item.code === draft.product_code)?.key ?? "market.sellTitle")}</strong><span>{draft.quantity_available} {draft.unit_code}</span></div>
      </div>
      <div className="sell-workspace">
        <div className="results-heading"><div><h2 id="sell-title">{t("market.sellTitle")}</h2><p>{t("market.sellSubtitle")}</p></div><Store size={22} /></div>
        {!canPublish ? <div className="seller-permission" role="note"><ShieldCheck size={20} /><span>{t("market.sellPermission")}</span></div> : null}
        <form className="sell-form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}>
          <label className="sell-product-field"><span>{t("market.listingProduct")}</span><select value={draft.product_code} onChange={(event) => selectProduct(event.target.value)}>{productPresets.map((preset) => <option key={preset.code} value={preset.code}>{t(preset.key)}</option>)}</select></label>
          <label className="sell-description-field"><span>{t("market.listingDescription")}</span><input value={draft.description} onChange={(event) => setDraft((value) => ({ ...value, description: event.target.value }))} required /></label>
          <label><span>{t("market.quantity")}</span><input inputMode="decimal" value={draft.quantity_available} onChange={(event) => setDraft((value) => ({ ...value, quantity_available: event.target.value }))} required /></label>
          <label><span>{t("market.unit")}</span><select value={draft.unit_code} onChange={(event) => setDraft((value) => ({ ...value, unit_code: event.target.value }))}><option value="KG">kg</option><option value="L">L</option><option value="PCS">pcs</option></select></label>
          <label><span>{t("market.listingMinimum")}</span><input inputMode="decimal" value={draft.minimum_batch} onChange={(event) => setDraft((value) => ({ ...value, minimum_batch: event.target.value }))} required /></label>
          <label><span>{t("market.listingPrice")}</span><input inputMode="decimal" value={draft.unit_price} onChange={(event) => setDraft((value) => ({ ...value, unit_price: event.target.value }))} required /></label>
          <label className="sell-origin-field"><span>{t("market.listingOrigin")}</span><input value={draft.origin_region} onChange={(event) => setDraft((value) => ({ ...value, origin_region: event.target.value }))} required /></label>
          <label><span>{t("market.listingUntil")}</span><input type="date" min={new Date(Date.now() + 24 * 60 * 60_000).toISOString().slice(0, 10)} value={draft.available_until} onChange={(event) => setDraft((value) => ({ ...value, available_until: event.target.value }))} required /></label>
          <button className="buy-button sell-submit" type="submit" disabled={!canPublish || mutation.isPending}>{mutation.isPending ? <RefreshCw className="spin" size={18} /> : <Store size={18} />}{mutation.isPending ? t("market.publishing") : t("market.publish")}</button>
        </form>
        {mutation.isError ? <p className="form-error" role="alert">{errorText(mutation.error)}</p> : null}
        {mutation.isSuccess ? <div className="sell-success" role="status"><CircleCheck size={22} /><div><strong>{t("market.published")}</strong><button type="button" onClick={() => onViewMarket(draft.product_code, draft.unit_code)}>{t("market.viewMarket")}</button></div></div> : null}
      </div>
    </section>
  );
}
function ProductCard({ candidate, busy, onBuy, onVerify }: { candidate: SearchCandidate; busy: boolean; onBuy: () => void; onVerify: () => void }) {
  const { t } = useTranslation();
  const sharesUnit = t("market.sharesUnit");
  const quote = candidate.quote;
  const product = productPresets.find((preset) => preset.code === candidate.offer.product_code);
  const title = product ? t(product.key) : candidate.offer.description;
  const canBuy = Boolean(quote && candidate.landed_cost && candidate.signature_verified && candidate.freshness !== "STALE");
  return (
    <article className="product-card">
      <div className={`product-photo ${visualFor(candidate.offer.product_code)}`} role="img" aria-label={title} />
      <div className="product-card-body">
        <div className="product-badges"><StatusBadge value={candidate.freshness} label={t(`market.freshness.${candidate.freshness}`)} />{candidate.signature_verified ? <span className="trust-mark"><CircleCheck size={14} />{t("market.trusted")}</span> : null}</div>
        <h3>{title}</h3>
        <p className="product-description">{candidate.offer.description}</p>
        <p className="seller-line"><strong>{t("market.seller")}:</strong> {candidate.offer.seller_ref} · {candidate.offer.home_node_code}</p>
        <dl className="stock-facts"><div><dt>{t("market.available")}</dt><dd>{formatAmount(candidate.offer.quantity_available, candidate.offer.unit_code)}</dd></div><div><dt>{t("market.minimum")}</dt><dd>{formatAmount(candidate.offer.minimum_batch, candidate.offer.unit_code)}</dd></div></dl>
        <div className="delivery-line"><Truck size={17} /><span>{quote ? `${t("market.deliveryIncluded")} · ${formatLocalDateTime(quote.delivery_until)}` : t("market.deliveryUnavailable")}</span></div>
        <div className="product-price"><span>{t("market.total")}</span><strong>{formatAmount(candidate.landed_cost, candidate.offer.valuation_unit, sharesUnit)}</strong><small>{formatAmount(candidate.offer.unit_price, candidate.offer.valuation_unit, sharesUnit)} / {candidate.offer.unit_code}</small></div>
        <details className="price-details"><summary>{t("market.costDetails")}</summary><dl><div><dt>{t("market.goods")}</dt><dd>{formatAmount(candidate.goods_cost, candidate.offer.valuation_unit, sharesUnit)}</dd></div><div><dt>{t("market.logistics")}</dt><dd>{formatAmount(candidate.logistics_cost, candidate.offer.valuation_unit, sharesUnit)}</dd></div><div><dt>{t("market.mandatory")}</dt><dd>{formatAmount(candidate.mandatory_cost, candidate.offer.valuation_unit, sharesUnit)}</dd></div>{Object.entries(quote?.cost_components ?? {}).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{formatAmount(String(value), candidate.offer.valuation_unit, sharesUnit)}</dd></div>)}</dl></details>
        <div className="product-actions"><button className="verify-button" type="button" title={t("market.verify")} onClick={onVerify} disabled={busy}><RefreshCw size={17} /></button><button className="buy-button" type="button" onClick={onBuy} disabled={busy || !canBuy}><ShoppingCart size={18} />{canBuy ? t("market.buy") : t("market.unavailable")}</button></div>
      </div>
    </article>
  );
}

function OrderPanel({ intents, receipts, selectedId, busy, onSelect, onReserve, onCommit, onCancel }: { intents: PurchaseIntent[]; receipts: Array<{ id: string; kind: string; home_node_code: string; status: string; expires_at: string; receipt_hash: string }>; selectedId: string; busy: boolean; onSelect: (id: string) => void; onReserve: (intent: PurchaseIntent, kind: "goods" | "logistics") => void; onCommit: (intent: PurchaseIntent) => void; onCancel: (intent: PurchaseIntent) => void }) {
  const { t } = useTranslation();
  const sharesUnit = t("market.sharesUnit");
  return (
    <div className="orders-layout">
      <section className="market-results">
        <div className="results-heading"><div><h2>{t("market.ordersTitle")}</h2><p>{t("market.ordersTab")}</p></div><span>{intents.length}</span></div>
        {!intents.length ? <div className="empty-market"><ListChecks size={28} /><strong>{t("market.noOrders")}</strong></div> : <div className="order-list">{intents.map((intent) => {
          const breakdown = intent.landed_cost_breakdown as { landed_cost?: string };
          return <article className={`order-card ${selectedId === intent.id ? "selected" : ""}`} key={intent.id} onClick={() => onSelect(intent.id)}><div className="order-icon"><PackageCheck size={22} /></div><div className="order-main"><StatusBadge value={intent.status} label={t(`market.intent.${intent.status}`)} /><strong>{formatAmount(intent.quantity, intent.unit_code)}</strong><span>{intent.destination_region} · {intent.id.slice(0, 8)}</span></div><div className="order-price"><span>{t("market.orderTotal")}</span><strong>{formatAmount(breakdown.landed_cost ?? intent.max_landed_cost, "COOP", sharesUnit)}</strong><small>{formatLocalDateTime(intent.expires_at)}</small></div><div className="order-actions">{intent.status === "PREPARING" ? <button className="compact-command" disabled={busy} onClick={(event) => { event.stopPropagation(); onReserve(intent, "goods"); }}>{t("market.reserveGoods")}</button> : null}{intent.status === "GOODS_RESERVED" ? <button className="compact-command" disabled={busy} onClick={(event) => { event.stopPropagation(); onReserve(intent, "logistics"); }}>{t("market.reserveDelivery")}</button> : null}{["PREPARED", "COMMITTING"].includes(intent.status) ? <button className="compact-command" disabled={busy} onClick={(event) => { event.stopPropagation(); onCommit(intent); }}>{t("market.confirm")}</button> : null}{["PREPARING", "GOODS_RESERVED", "PREPARED", "CANCELLING"].includes(intent.status) ? <button className="icon-button" title={t("common.cancel")} disabled={busy} onClick={(event) => { event.stopPropagation(); onCancel(intent); }}><XCircle size={17} /></button> : null}</div></article>;
        })}</div>}
      </section>
      {selectedId ? <section className="panel receipt-panel"><div className="panel-heading"><h2>{t("market.signedReservations")}</h2><span>{receipts.length}</span></div><div className="rows">{receipts.map((receipt) => <div className="data-row" key={receipt.id}><strong>{receipt.kind}</strong><span>{receipt.home_node_code}<small>{receipt.receipt_hash.slice(7, 19)}</small></span><StatusBadge value={receipt.status} label={receipt.status} /><time>{formatLocalDateTime(receipt.expires_at)}</time></div>)}</div></section> : null}
    </div>
  );
}