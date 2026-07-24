import {
  Archive,
  ChevronDown,
  CircleCheck,
  Database,
  ImagePlus,
  ListChecks,
  MapPin,
  PackageCheck,
  Phone,
  RadioTower,
  RefreshCw,
  Search,
  ShieldCheck,
  ShoppingCart,
  Store,
  Wrench,
  Truck,
  XCircle,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { AdminApiError, type Principal } from "./api/admin";
import {
  cancelPurchase,
  commitPurchase,
  createPurchaseIntent,
  getMyLogisticsQuotes,
  getPurchaseIntents,
  getReservationReceipts,
  publishLogisticsQuote,
  publishOffer,
  reservePurchase,
  searchCatalog,
  verifyOffer,
  type DeliveryDetails,
  type LogisticsQuoteDraft,
  type OfferDraft,
  type PurchaseIntent,
  type SearchCandidate,
  type SearchFilters,
  type SearchMode,
} from "./api/discovery";
import { uploadEvidence } from "./api/inventory";
import {
  getOfferImage,
  getParticipantAddresses,
  type ParticipantAddress,
} from "./api/participant";
import "./i18n";
import { userErrorMessage } from "./shared/api-error";
import { formatLocalDateTime } from "./shared/date-time";
import "./discovery.css";

type Section = "search" | "sell" | "logistics" | "intents";
type ProductPreset = {
  code: string;
  key: string;
  unit: string;
  visual: string;
  aliases: string[];
  kind: "PRODUCT" | "SERVICE";
};

const productPresets: ProductPreset[] = [
  { code: "CABBAGE.WHITE", key: "market.cabbage", unit: "KG", visual: "cabbage", aliases: ["капуста", "cabbage"], kind: "PRODUCT" },
  { code: "NAIL.STEEL.100MM", key: "market.nails", unit: "PCS", visual: "nails", aliases: ["гвозди", "гвоздь", "nails", "nail"], kind: "PRODUCT" },
  { code: "MILK.UHT.3_2", key: "market.milk", unit: "L", visual: "milk", aliases: ["молоко", "milk"], kind: "PRODUCT" },
  {
    code: "SERVICE.COMPUTER.REPAIR",
    key: "market.computerRepair",
    unit: "HOUR",
    visual: "service",
    aliases: ["ремонт компьютера", "починить компьютер", "ремонт компьютеров", "computer repair", "fix computer"],
    kind: "SERVICE",
  },
];

const marketErrorKeys: Record<string, string> = {
  OFFER_NOT_RESERVABLE: "market.error.offerUnavailable",
  PEER_TRANSPORT_UNAVAILABLE: "market.error.nodeUnavailable",
  PEER_NOT_TRUSTED: "market.error.peerNotTrusted",
  PURCHASE_QUANTITY_INVALID: "market.error.quantityInvalid",
};
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
  return userErrorMessage(error);
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

function deliveryDetails(address: ParticipantAddress): DeliveryDetails {
  return {
    address_text: address.address_text,
    contact_name: address.contact_name,
    contact_phone: address.contact_phone,
    instructions: address.instructions ?? "",
  };
}

export default function DiscoveryView({ principal, initialSection = "search" }: { principal: Principal; initialSection?: Section }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [section, setSection] = useState<Section>(initialSection);
  useEffect(() => setSection(initialSection), [initialSection]);
  const [filters, setFilters] = useState<SearchFilters>(initialFilters);
  const [productQuery, setProductQuery] = useState(t("market.cabbage"));
  const [submitted, setSubmitted] = useState<SearchFilters | null>(initialFilters);
  const [selectedIntentId, setSelectedIntentId] = useState("");
  const [quoteCandidate, setQuoteCandidate] = useState<SearchCandidate | null>(null);
  const [checkoutCandidate, setCheckoutCandidate] = useState<SearchCandidate | null>(null);
  const [delivery, setDelivery] = useState<DeliveryDetails>({
    address_text: "",
    contact_name: "",
    contact_phone: "",
    instructions: "",
  });
  const [deliveryAddressId, setDeliveryAddressId] = useState("");
  const canQuote = principal.roles.some((grant) => ["LOGISTICS_OPERATOR", "NODE_BUSINESS_OPERATOR"].includes(grant.role));
  const addressBook = useQuery({
    queryKey: ["participant-addresses"],
    queryFn: getParticipantAddresses,
  });
  const savedAddresses = addressBook.data ?? [];
  const deliveryAddresses = savedAddresses.filter((address) =>
    ["DELIVERY", "BOTH"].includes(address.purpose),
  );
  const selectedDeliveryAddress = deliveryAddresses.find((address) => address.id === deliveryAddressId) ?? null;

  useEffect(() => {
    if (deliveryAddressId || delivery.address_text || !deliveryAddresses.length) return;
    const address = deliveryAddresses.find((item) => item.is_default_delivery) ?? deliveryAddresses[0];
    if (!address) return;
    setDeliveryAddressId(address.id);
    setDelivery(deliveryDetails(address));
    setFilters((value) => ({ ...value, destination_region: address.region_code }));
  }, [delivery.address_text, deliveryAddressId, deliveryAddresses]);

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
    mutationFn: ({ candidate, details }: { candidate: SearchCandidate; details: DeliveryDetails }) =>
      createPurchaseIntent(candidate, submitted?.quantity ?? filters.quantity, details),
    onSuccess: async (result) => {
      setSelectedIntentId(result.object_id);
      setCheckoutCandidate(null);
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

  function chooseDeliveryAddress(addressId: string) {
    setDeliveryAddressId(addressId);
    const address = deliveryAddresses.find((item) => item.id === addressId);
    if (!address) {
      setDelivery({ address_text: "", contact_name: "", contact_phone: "", instructions: "" });
      return;
    }
    setDelivery(deliveryDetails(address));
    setFilters((value) => ({ ...value, destination_region: address.region_code }));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    prepare.reset();
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
    prepare.reset();
    const next = {
      ...filters,
      product_code: preset.code,
      unit_code: preset.unit,
      quantity: preset.kind === "SERVICE" ? "1.000" : filters.quantity,
    };
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
          {canQuote ? <button className={section === "logistics" ? "active" : ""} onClick={() => setSection("logistics")} role="tab" aria-selected={section === "logistics"}>
            <Truck size={16} /><span>{t("market.logisticsTab")}</span>
          </button> : null}
          <button className={section === "intents" ? "active" : ""} onClick={() => setSection("intents")} role="tab" aria-selected={section === "intents"}>
            <ListChecks size={16} /><span>{t("market.ordersTab")}</span>{activeIntents ? <b>{activeIntents}</b> : null}
          </button>
        </div>
      </header>

      {combinedError ? <p className="form-error discovery-error" role="alert">{combinedError instanceof AdminApiError && marketErrorKeys[combinedError.code] ? t(marketErrorKeys[combinedError.code]!) : errorText(combinedError)}</p> : null}

      {section === "search" ? (
        <>
          <section className="market-search" aria-label={t("market.find")}>
            <form onSubmit={submit}>
              <label className="product-query">
                <span>{t("market.searchLabel")}</span>
                <div className="search-input-wrap"><Search size={20} /><input value={productQuery} onChange={(event) => setProductQuery(event.target.value)} placeholder={t("market.searchPlaceholder")} required /></div>
              </label>
              <label><span>{t("market.quantity")}</span><input inputMode="decimal" value={filters.quantity} onChange={(event) => setFilters((value) => ({ ...value, quantity: event.target.value }))} required /></label>
              <label><span>{t("market.unit")}</span><select value={filters.unit_code} onChange={(event) => setFilters((value) => ({ ...value, unit_code: event.target.value }))}><option value="KG">{t("units.kg")}</option><option value="L">{t("units.l")}</option><option value="PCS">{t("units.pcs")}</option><option value="HOUR">{t("units.hour")}</option></select></label>
              <label className="destination-query"><span>{t("market.deliveryPoint")}</span>{deliveryAddresses.length ? <select value={deliveryAddressId} onChange={(event) => chooseDeliveryAddress(event.target.value)}><option value="">{t("market.enterAddressManually")}</option>{deliveryAddresses.map((address) => <option data-i18n-ignore="true" key={address.id} value={address.id}>{address.label} · {address.region_code}</option>)}</select> : null}{selectedDeliveryAddress ? <small data-i18n-ignore="true"><MapPin size={13} />{selectedDeliveryAddress.address_text}</small> : <input value={filters.destination_region} onChange={(event) => setFilters((value) => ({ ...value, destination_region: event.target.value }))} placeholder={t("market.destinationPlaceholder")} required />}</label>
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
            <article className="metric"><Truck size={19} /><span>{t("market.readyToExchange")}</span><strong>{candidates.filter((item) => item.quote).length}</strong></article>
            <article className="metric"><ListChecks size={19} /><span>{t("market.activeOrders")}</span><strong>{activeIntents}</strong></article>
          </section>

          {peerStatuses.length ? <div className="peer-status-list" aria-label={t("market.source")}>{peerStatuses.map((peer) => <span className={`peer-status ${peer.status.toLowerCase()}`} key={peer.node_code}><RadioTower size={14} />{peer.node_code}: {peer.status === "SUCCEEDED" ? `${peer.imported_offers} ${t("market.peerReceived")}` : peer.result_code}</span>)}</div> : null}

          <section className="market-results" aria-labelledby="offers-title">
            <div className="results-heading"><div><h2 id="offers-title">{t("market.offers")}</h2><p>{principal.login}</p></div><span>{candidates.length}</span></div>
            {results.isPending ? <div className="state"><RefreshCw className="spin" size={22} />{t("market.searching")}</div> : null}
            {!results.isPending && candidates.length === 0 ? <div className="empty-market"><Search size={28} /><strong>{t("market.noOffers")}</strong></div> : null}
            {candidates.length ? <div className="product-grid">{candidates.map((candidate) => <ProductCard candidate={candidate} key={`${candidate.offer.record_id}:${candidate.quote?.record_id ?? "none"}`} busy={prepare.isPending || verify.isPending} onBuy={() => { prepare.reset(); setCheckoutCandidate(candidate); }} onVerify={() => verify.mutate(candidate.offer.record_id)} onQuote={canQuote ? () => { setQuoteCandidate(candidate); setSection("logistics"); } : undefined} />)}</div> : null}
          </section>
          {checkoutCandidate ? <DeliveryCheckout candidate={checkoutCandidate} details={delivery} addresses={deliveryAddresses} selectedAddressId={deliveryAddressId} busy={prepare.isPending} error={prepare.error} onChange={setDelivery} onSelectAddress={chooseDeliveryAddress} onCancel={() => setCheckoutCandidate(null)} onConfirm={() => prepare.mutate({ candidate: checkoutCandidate, details: delivery })} /> : null}
        </>
      ) : section === "sell" ? (
        <SellPanel
          principal={principal}
          addresses={savedAddresses}
          onViewMarket={(productCode, unitCode) => {
            const preset = productPresets.find((item) => item.code === productCode);
            const next = { ...filters, product_code: productCode, unit_code: unitCode };
            setProductQuery(preset ? t(preset.key) : productCode);
            setFilters(next);
            setSubmitted(next);
            setSection("search");
          }}
        />
      ) : section === "logistics" && canQuote ? (
        <LogisticsQuotePanel
          principal={principal}
          candidate={quoteCandidate ?? candidates[0] ?? null}
          candidates={candidates}
          destination={filters.destination_region}
          requestedQuantity={filters.quantity}
          onSelect={setQuoteCandidate}
          onSearch={() => setSection("search")}
        />
      ) : (
        <OrderPanel intents={intentRows} receipts={receipts.data ?? []} selectedId={selectedIntentId} busy={reserve.isPending || commit.isPending || cancel.isPending} onSelect={setSelectedIntentId} onReserve={(intent, kind) => reserve.mutate({ intentId: intent.id, kind })} onCommit={(intent) => commit.mutate(intent)} onCancel={(intent) => cancel.mutate(intent)} />
      )}
    </div>
  );
}

function DeliveryCheckout({ candidate, details, addresses, selectedAddressId, busy, error, onChange, onSelectAddress, onCancel, onConfirm }: {
  candidate: SearchCandidate;
  details: DeliveryDetails;
  addresses: ParticipantAddress[];
  selectedAddressId: string;
  busy: boolean;
  error: unknown;
  onChange: (details: DeliveryDetails) => void;
  onSelectAddress: (addressId: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  const preset = productPresets.find((item) => item.code === candidate.offer.product_code);
  const title = preset ? t(preset.key) : candidate.offer.description;
  const compatibleAddresses = addresses.filter(
    (address) => address.region_code === candidate.quote?.destination_region,
  );
  return <div className="delivery-checkout-backdrop" role="presentation">
    <section className="delivery-checkout" role="dialog" aria-modal="true" aria-labelledby="delivery-checkout-title">
      <div className="delivery-checkout-heading">
        <div><span className="eyebrow">{t("market.checkoutEyebrow")}</span><h2 id="delivery-checkout-title">{t("market.checkoutTitle")}</h2><p>{t("market.checkoutHint")}</p></div>
        <button className="icon-button" type="button" title={t("common.cancel")} onClick={onCancel}><XCircle size={20} /></button>
      </div>
      <div className="checkout-route">
        <div><Store size={20} /><span>{t("market.checkoutItem")}</span><strong>{title}</strong></div>
        <div><Truck size={20} /><span>{t("market.publicDeliveryArea")}</span><strong>{candidate.quote?.destination_region}</strong></div>
      </div>
      <form className="delivery-checkout-form" onSubmit={(event) => { event.preventDefault(); onConfirm(); }}>
        {compatibleAddresses.length ? <label className="span-two saved-address-select"><span>{t("market.savedDeliveryPoint")}</span><select value={compatibleAddresses.some((address) => address.id === selectedAddressId) ? selectedAddressId : ""} onChange={(event) => onSelectAddress(event.target.value)}><option value="">{t("market.enterAddressManually")}</option>{compatibleAddresses.map((address) => <option data-i18n-ignore="true" key={address.id} value={address.id}>{address.label} · {address.address_text}</option>)}</select><small>{t("market.addressSnapshotHint")}</small></label> : null}
        <label className="span-two"><span>{t("market.deliveryAddress")}</span><span className="field-with-icon"><MapPin size={18} /><input value={details.address_text} onChange={(event) => onChange({ ...details, address_text: event.target.value })} placeholder={t("market.deliveryAddressPlaceholder")} required minLength={5} /></span><small>{t("market.deliveryAddressHint")}</small></label>
        <label><span>{t("market.deliveryContact")}</span><input value={details.contact_name} onChange={(event) => onChange({ ...details, contact_name: event.target.value })} placeholder={t("market.contactNamePlaceholder")} required minLength={2} /></label>
        <label><span>{t("market.contactPhone")}</span><span className="field-with-icon"><Phone size={18} /><input type="tel" value={details.contact_phone} onChange={(event) => onChange({ ...details, contact_phone: event.target.value })} placeholder={t("market.contactPhonePlaceholder")} required minLength={5} /></span></label>
        <label className="span-two"><span>{t("market.deliveryInstructions")}</span><textarea value={details.instructions} onChange={(event) => onChange({ ...details, instructions: event.target.value })} placeholder={t("market.deliveryInstructionsPlaceholder")} rows={3} /></label>
        {error ? <p className="form-error span-two" role="alert">{errorText(error)}</p> : null}
        <div className="checkout-actions span-two"><button className="secondary-button" type="button" onClick={onCancel}>{t("common.cancel")}</button><button className="buy-button" type="submit" disabled={busy}>{busy ? <RefreshCw className="spin" size={18} /> : <ShoppingCart size={18} />}{t("market.confirmDeliveryAddress")}</button></div>
      </form>
    </section>
  </div>;
}

function dateTimeInput(offsetMs: number): string {
  const date = new Date(Date.now() + offsetMs);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

function LogisticsQuotePanel({ principal, candidate, candidates, destination, requestedQuantity, onSelect, onSearch }: {
  principal: Principal;
  candidate: SearchCandidate | null;
  candidates: SearchCandidate[];
  destination: string;
  requestedQuantity: string;
  onSelect: (candidate: SearchCandidate | null) => void;
  onSearch: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const quotes = useQuery({ queryKey: ["my-logistics-quotes"], queryFn: getMyLogisticsQuotes });
  const [draft, setDraft] = useState<LogisticsQuoteDraft>(() => ({
    offer_record_id: candidate?.offer.record_id ?? "",
    destination_region: destination,
    capacity: requestedQuantity,
    transport_cost: "8.00",
    handling_cost: "1.00",
    delivery_from: dateTimeInput(24 * 60 * 60_000),
    delivery_until: dateTimeInput(48 * 60 * 60_000),
    liability_limit: "100.00",
    valid_until: dateTimeInput(7 * 24 * 60 * 60_000),
  }));
  useEffect(() => {
    setDraft((value) => ({
      ...value,
      offer_record_id: candidate?.offer.record_id ?? "",
      destination_region: destination,
      capacity: requestedQuantity,
    }));
  }, [candidate?.offer.record_id, destination, requestedQuantity]);
  const mutation = useMutation({
    mutationFn: () => publishLogisticsQuote(draft, principal.login),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["my-logistics-quotes"] }),
        queryClient.invalidateQueries({ queryKey: ["federated-search"] }),
      ]);
    },
  });
  const ownQuotes = quotes.data ?? [];
  return <div className="logistics-quote-layout">
    <section className="logistics-quote-workspace">
      <div className="results-heading"><div><h2>{t("market.logisticsTitle")}</h2><p>{t("market.logisticsSubtitle")}</p></div><Truck size={24} /></div>
      {!candidate ? <div className="empty-market"><Search size={28} /><strong>{t("market.logisticsFindFirst")}</strong><button className="secondary-button" type="button" onClick={onSearch}>{t("market.openCatalog")}</button></div> : <>
        <label className="logistics-offer-select"><span>{t("market.logisticsOffer")}</span><select value={candidate.offer.record_id} onChange={(event) => onSelect(candidates.find((item) => item.offer.record_id === event.target.value) ?? null)}>{candidates.map((item) => <option key={item.offer.record_id} value={item.offer.record_id}>{item.offer.description} · {item.offer.origin_region}</option>)}</select></label>
        <div className="logistics-route-summary"><div><span>{t("market.routeFrom")}</span><strong>{candidate.offer.origin_region}</strong></div><Truck size={22} /><div><span>{t("market.routeTo")}</span><strong>{draft.destination_region}</strong></div></div>
        <form className="sell-form logistics-quote-form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}>
          <label className="span-two"><span>{t("market.destination")}</span><input value={draft.destination_region} onChange={(event) => setDraft((value) => ({ ...value, destination_region: event.target.value }))} required /></label>
          <label><span>{t("market.logisticsCapacity")}</span><input inputMode="decimal" value={draft.capacity} onChange={(event) => setDraft((value) => ({ ...value, capacity: event.target.value }))} required /></label>
          <label><span>{t("market.unit")}</span><input value={t(`units.${candidate.offer.unit_code.toLowerCase()}`, { defaultValue: candidate.offer.unit_code })} disabled /></label>
          <label><span>{t("market.transportCost")}</span><input inputMode="decimal" value={draft.transport_cost} onChange={(event) => setDraft((value) => ({ ...value, transport_cost: event.target.value }))} required /></label>
          <label><span>{t("market.handlingCost")}</span><input inputMode="decimal" value={draft.handling_cost} onChange={(event) => setDraft((value) => ({ ...value, handling_cost: event.target.value }))} required /></label>
          <label><span>{t("market.pickupBy")}</span><input type="datetime-local" value={draft.delivery_from} onChange={(event) => setDraft((value) => ({ ...value, delivery_from: event.target.value }))} required /></label>
          <label><span>{t("market.deliverBy")}</span><input type="datetime-local" value={draft.delivery_until} onChange={(event) => setDraft((value) => ({ ...value, delivery_until: event.target.value }))} required /></label>
          <label><span>{t("market.liabilityLimit")}</span><input inputMode="decimal" value={draft.liability_limit} onChange={(event) => setDraft((value) => ({ ...value, liability_limit: event.target.value }))} required /></label>
          <label><span>{t("market.quoteValidUntil")}</span><input type="datetime-local" value={draft.valid_until} onChange={(event) => setDraft((value) => ({ ...value, valid_until: event.target.value }))} required /></label>
          <button className="buy-button sell-submit" type="submit" disabled={mutation.isPending}>{mutation.isPending ? <RefreshCw className="spin" size={18} /> : <Truck size={18} />}{t(mutation.isPending ? "market.quotePublishing" : "market.publishQuote")}</button>
        </form>
        {mutation.isError ? <p className="form-error" role="alert">{errorText(mutation.error)}</p> : null}
        {mutation.isSuccess ? <div className="sell-success" role="status"><CircleCheck size={22} /><div><strong>{t("market.quotePublished")}</strong><button type="button" onClick={onSearch}>{t("market.viewMarket")}</button></div></div> : null}
      </>}
    </section>
    <section className="logistics-own-quotes">
      <div className="panel-heading"><h2>{t("market.myQuotes")}</h2><span>{ownQuotes.length}</span></div>
      {quotes.isPending ? <div className="state"><RefreshCw className="spin" size={20} />{t("common.loading")}</div> : !ownQuotes.length ? <div className="state"><Truck size={21} />{t("market.noQuotes")}</div> : <div className="rows">{ownQuotes.map((quote) => <article className="logistics-quote-row" key={quote.record_id}><div><strong>{quote.origin_region} → {quote.destination_region}</strong><span>{formatAmount(quote.capacity, t(`units.${quote.unit_code.toLowerCase()}`, { defaultValue: quote.unit_code }))}</span></div><div><span>{t("market.deliveryCost")}</span><strong>{Object.values(quote.cost_components).reduce<number>((sum, value) => sum + Number(value), 0).toLocaleString(document.documentElement.lang, { maximumFractionDigits: 2 })} {t("market.sharesUnit")}</strong></div><StatusBadge value="ACTIVE" label={t("market.quoteActive")} /></article>)}</div>}
    </section>
  </div>;
}
function SellPanel({ principal, addresses, onViewMarket }: { principal: Principal; addresses: ParticipantAddress[]; onViewMarket: (productCode: string, unitCode: string) => void }) {
  const { t } = useTranslation();
  const [file, setFile] = useState<File | null>(null);
  const imagePreview = useMemo(() => file ? URL.createObjectURL(file) : null, [file]);
  useEffect(() => () => { if (imagePreview) URL.revokeObjectURL(imagePreview); }, [imagePreview]);
  const [draft, setDraft] = useState<OfferDraft>(() => ({
    kind: "PRODUCT",
    product_code: "MILK.UHT.3_2",
    description: t("market.milk"),
    quantity_available: "100.000",
    unit_code: "L",
    minimum_batch: "10.000",
    origin_region: "EAST-DISTRICT",
    pickup_address_text: "",
    pickup_contact_name: principal.login,
    pickup_contact_phone: "",
    pickup_instructions: "",
    unit_price: "3.00",
    available_until: new Date(Date.now() + 7 * 24 * 60 * 60_000).toISOString().slice(0, 10),
    image_evidence_id: null,
  }));
  const [pickupAddressId, setPickupAddressId] = useState("");
  const pickupAddresses = addresses.filter((address) => ["PICKUP", "BOTH"].includes(address.purpose));
  useEffect(() => {
    if (pickupAddressId || draft.pickup_address_text || !pickupAddresses.length) return;
    const address = pickupAddresses.find((item) => item.is_default_pickup) ?? pickupAddresses[0];
    if (!address) return;
    setPickupAddressId(address.id);
    setDraft((value) => ({
      ...value,
      origin_region: address.region_code,
      pickup_address_text: address.address_text,
      pickup_contact_name: address.contact_name,
      pickup_contact_phone: address.contact_phone,
      pickup_instructions: address.instructions ?? "",
    }));
  }, [draft.pickup_address_text, pickupAddressId, pickupAddresses]);
  const canPublish = principal.roles.some((grant) => ["EXCHANGE_PARTICIPANT", "NODE_BUSINESS_OPERATOR"].includes(grant.role));
  const cooperativeId = principal.roles.find((grant) => grant.cooperative_id)?.cooperative_id ?? null;
  const mutation = useMutation({
    mutationFn: async () => {
      if (!cooperativeId) throw new Error("COOPERATIVE_MEMBERSHIP_REQUIRED");
      const imageEvidenceId = file
        ? await uploadEvidence(cooperativeId, file, "OFFER_IMAGE")
        : null;
      const normalizedTitle = draft.description.toUpperCase().replace(/[^A-Z0-9]+/g, ".").replace(/^\.|\.$/g, "");
      const productCode = draft.kind === "SERVICE"
        ? draft.product_code === "SERVICE.CUSTOM"
          ? `SERVICE.${normalizedTitle || Date.now()}`.slice(0, 80)
          : draft.product_code
        : draft.product_code === "CUSTOM.PRODUCT"
          ? `PRODUCT.${normalizedTitle || Date.now()}`.slice(0, 80)
          : draft.product_code;
      return publishOffer(
        { ...draft, product_code: productCode, image_evidence_id: imageEvidenceId },
        principal.member_id ?? principal.login,
      );
    },
  });

  function selectPickupAddress(addressId: string) {
    setPickupAddressId(addressId);
    const address = pickupAddresses.find((item) => item.id === addressId);
    if (!address) {
      setDraft((value) => ({
        ...value,
        pickup_address_text: "",
        pickup_contact_name: principal.login,
        pickup_contact_phone: "",
        pickup_instructions: "",
      }));
      return;
    }
    setDraft((value) => ({
      ...value,
      origin_region: address.region_code,
      pickup_address_text: address.address_text,
      pickup_contact_name: address.contact_name,
      pickup_contact_phone: address.contact_phone,
      pickup_instructions: address.instructions ?? "",
    }));
  }

  function selectKind(kind: "PRODUCT" | "SERVICE") {
    setDraft((value) => ({
      ...value,
      kind,
      product_code: kind === "SERVICE" ? "SERVICE.COMPUTER.REPAIR" : "MILK.UHT.3_2",
      description: kind === "SERVICE" ? t("market.computerRepair") : t("market.milk"),
      unit_code: kind === "SERVICE" ? "HOUR" : "L",
      quantity_available: kind === "SERVICE" ? "8.00" : "100.000",
      minimum_batch: kind === "SERVICE" ? "1.00" : "10.000",
    }));
    mutation.reset();
  }

  function selectProduct(productCode: string) {
    if (["CUSTOM.PRODUCT", "SERVICE.CUSTOM"].includes(productCode)) {
      setDraft((value) => ({ ...value, product_code: productCode, description: "" }));
      mutation.reset();
      return;
    }
    const preset = productPresets.find((item) => item.code === productCode) ?? productPresets[0];
    if (!preset) return;
    setDraft((value) => ({
      ...value,
      product_code: preset.code,
      unit_code: preset.unit,
      description: t(preset.key),
      minimum_batch: preset.unit === "PCS" ? "100" : preset.unit === "HOUR" ? "1.00" : "10.000",
    }));
    mutation.reset();
  }

  const visual = draft.kind === "SERVICE" ? "service" : visualFor(draft.product_code);
  return (
    <section className="sell-layout" aria-labelledby="sell-title">
      <div className="sell-product-visual">
        {imagePreview
          ? <img className="uploaded-product-photo" src={imagePreview} alt={draft.description} />
          : <div className={`product-photo ${visual}`} role="img" aria-label={draft.description || t(draft.kind === "SERVICE" ? "market.sellServiceTitle" : "market.sellTitle")} />}
        <div><strong>{draft.description || t(draft.kind === "SERVICE" ? "market.service" : "market.otherProduct")}</strong><span>{draft.quantity_available} {t(`units.${draft.unit_code.toLowerCase()}`, { defaultValue: draft.unit_code })}</span></div>
      </div>
      <div className="sell-workspace">
        <div className="results-heading"><div><h2 id="sell-title">{t(draft.kind === "SERVICE" ? "market.sellServiceTitle" : "market.sellTitle")}</h2><p>{t(draft.kind === "SERVICE" ? "market.sellServiceSubtitle" : "market.sellSubtitle")}</p></div><Store size={22} /></div>
        {!canPublish ? <div className="seller-permission" role="note"><ShieldCheck size={20} /><span>{t("market.sellPermission")}</span></div> : null}
        <div className="offer-kind-switch" role="group" aria-label={t("market.offerKind")}>
          <button type="button" className={draft.kind === "PRODUCT" ? "active" : ""} onClick={() => selectKind("PRODUCT")}><Store size={17} />{t("market.product")}</button>
          <button type="button" className={draft.kind === "SERVICE" ? "active" : ""} onClick={() => selectKind("SERVICE")}><Wrench size={17} />{t("market.service")}</button>
        </div>
        <form className="sell-form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}>
          {draft.kind === "PRODUCT" ? <label className="sell-product-field"><span>{t("market.listingProduct")}</span><select value={draft.product_code} onChange={(event) => selectProduct(event.target.value)}>{productPresets.filter((preset) => preset.kind === "PRODUCT").map((preset) => <option key={preset.code} value={preset.code}>{t(preset.key)}</option>)}<option value="CUSTOM.PRODUCT">{t("market.otherProduct")}</option></select></label> : null}
          {draft.kind === "SERVICE" ? <label className="sell-product-field"><span>{t("market.listingService")}</span><select value={draft.product_code} onChange={(event) => selectProduct(event.target.value)}>{productPresets.filter((preset) => preset.kind === "SERVICE").map((preset) => <option key={preset.code} value={preset.code}>{t(preset.key)}</option>)}<option value="SERVICE.CUSTOM">{t("market.otherService")}</option></select></label> : null}
          <label className="sell-description-field"><span>{t(draft.kind === "SERVICE" ? "market.serviceName" : "market.listingDescription")}</span><input value={draft.description} onChange={(event) => setDraft((value) => ({ ...value, description: event.target.value }))} placeholder={t(draft.kind === "SERVICE" ? "market.serviceNamePlaceholder" : "market.productNamePlaceholder")} required /></label>
          <label className="sell-image-field"><span>{t("market.photo")}</span><span className="file-picker"><ImagePlus size={19} /><span>{file ? file.name : t("market.choosePhoto")}</span><input type="file" accept="image/jpeg,image/png,image/webp" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></span></label>
          <label><span>{t(draft.kind === "SERVICE" ? "market.capacity" : "market.quantity")}</span><input inputMode="decimal" value={draft.quantity_available} onChange={(event) => setDraft((value) => ({ ...value, quantity_available: event.target.value }))} required /></label>
          <label><span>{t("market.unit")}</span><select value={draft.unit_code} onChange={(event) => setDraft((value) => ({ ...value, unit_code: event.target.value }))}><option value="KG">{t("units.kg")}</option><option value="L">{t("units.l")}</option><option value="PCS">{t("units.pcs")}</option><option value="HOUR">{t("units.hour")}</option></select></label>
          <label><span>{t("market.listingMinimum")}</span><input inputMode="decimal" value={draft.minimum_batch} onChange={(event) => setDraft((value) => ({ ...value, minimum_batch: event.target.value }))} required /></label>
          <label><span>{t("market.listingPrice")}</span><input inputMode="decimal" value={draft.unit_price} onChange={(event) => setDraft((value) => ({ ...value, unit_price: event.target.value }))} required /></label>
          <label className="sell-origin-field"><span>{t(draft.kind === "SERVICE" ? "market.serviceArea" : "market.listingOrigin")}</span><input value={draft.origin_region} onChange={(event) => setDraft((value) => ({ ...value, origin_region: event.target.value }))} required /><small>{t("market.publicAreaHint")}</small></label>
          <label><span>{t("market.listingUntil")}</span><input type="date" min={new Date(Date.now() + 24 * 60 * 60_000).toISOString().slice(0, 10)} value={draft.available_until} onChange={(event) => setDraft((value) => ({ ...value, available_until: event.target.value }))} required /></label>
          <fieldset className="pickup-point-fields span-two"><legend>{t(draft.kind === "SERVICE" ? "market.serviceContactPoint" : "market.pickupPoint")}</legend><p>{t("market.pickupPrivacyHint")}</p>
            {pickupAddresses.length ? <label className="span-two saved-address-select"><span>{t("market.savedPickupPoint")}</span><select value={pickupAddressId} onChange={(event) => selectPickupAddress(event.target.value)}><option value="">{t("market.enterAddressManually")}</option>{pickupAddresses.map((address) => <option data-i18n-ignore="true" key={address.id} value={address.id}>{address.label} · {address.address_text}</option>)}</select><small>{t("market.addressSnapshotHint")}</small></label> : null}
            <label className="span-two"><span>{t(draft.kind === "SERVICE" ? "market.serviceAddress" : "market.pickupAddress")}</span><span className="field-with-icon"><MapPin size={18} /><input value={draft.pickup_address_text} onChange={(event) => setDraft((value) => ({ ...value, pickup_address_text: event.target.value }))} placeholder={t("market.pickupAddressPlaceholder")} required minLength={5} /></span></label>
            <label><span>{t("market.pickupContact")}</span><input value={draft.pickup_contact_name} onChange={(event) => setDraft((value) => ({ ...value, pickup_contact_name: event.target.value }))} placeholder={t("market.contactNamePlaceholder")} required minLength={2} /></label>
            <label><span>{t("market.contactPhone")}</span><span className="field-with-icon"><Phone size={18} /><input type="tel" value={draft.pickup_contact_phone} onChange={(event) => setDraft((value) => ({ ...value, pickup_contact_phone: event.target.value }))} placeholder={t("market.contactPhonePlaceholder")} required minLength={5} /></span></label>
            <label className="span-two"><span>{t("market.pickupInstructions")}</span><textarea value={draft.pickup_instructions} onChange={(event) => setDraft((value) => ({ ...value, pickup_instructions: event.target.value }))} placeholder={t("market.pickupInstructionsPlaceholder")} rows={3} /></label>
          </fieldset>
          <button className="buy-button sell-submit" type="submit" disabled={!canPublish || mutation.isPending}>{mutation.isPending ? <RefreshCw className="spin" size={18} /> : <Store size={18} />}{mutation.isPending ? t(file ? "market.uploadingAndPublishing" : "market.publishing") : t("market.publish")}</button>
        </form>
        {mutation.isError ? <p className="form-error" role="alert">{errorText(mutation.error)}</p> : null}
        {mutation.isSuccess ? <div className="sell-success" role="status"><CircleCheck size={22} /><div><strong>{t("market.published")}</strong><button type="button" onClick={() => onViewMarket(draft.product_code, draft.unit_code)}>{t("market.viewMarket")}</button></div></div> : null}
      </div>
    </section>
  );
}
function CatalogOfferImage({ candidate, title }: { candidate: SearchCandidate; title: string }) {
  const evidenceId = candidate.offer.handling_requirements.image_evidence_id;
  const [imageUrl, setImageUrl] = useState<string | null>(null);

  useEffect(() => {
    if (typeof evidenceId !== "string" || !evidenceId) return;
    let active = true;
    let objectUrl: string | null = null;
    void getOfferImage(candidate.offer.record_id).then((blob) => {
      if (!active) return;
      objectUrl = URL.createObjectURL(blob);
      setImageUrl(objectUrl);
    }).catch(() => undefined);
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [candidate.offer.record_id, evidenceId]);

  return imageUrl
    ? <img className="catalog-product-photo" src={imageUrl} alt={title} />
    : <div className={`product-photo ${visualFor(candidate.offer.product_code)}`} role="img" aria-label={title} />;
}
function ProductCard({ candidate, busy, onBuy, onVerify, onQuote }: { candidate: SearchCandidate; busy: boolean; onBuy: () => void; onVerify: () => void; onQuote?: () => void }) {
  const { t } = useTranslation();
  const sharesUnit = t("market.sharesUnit");
  const quote = candidate.quote;
  const product = productPresets.find((preset) => preset.code === candidate.offer.product_code);
  const title = product ? t(product.key) : candidate.offer.description;
  const isService = product?.kind === "SERVICE"
    || candidate.offer.handling_requirements.offer_kind === "SERVICE"
    || candidate.offer.product_code.startsWith("SERVICE.");
  const unitLabel = t(`units.${candidate.offer.unit_code.toLowerCase()}`, { defaultValue: candidate.offer.unit_code });
  const canBuy = Boolean(quote && candidate.landed_cost && candidate.signature_verified && candidate.freshness !== "STALE");
  return (
    <article className="product-card">
      <CatalogOfferImage candidate={candidate} title={title} />
      <div className="product-card-body">
        <div className="product-badges"><StatusBadge value={candidate.freshness} label={t(`market.freshness.${candidate.freshness}`)} />{candidate.signature_verified ? <span className="trust-mark"><CircleCheck size={14} />{t("market.trusted")}</span> : null}</div>
        <h3>{title}</h3>
        <p className="product-description">{candidate.offer.description}</p>
        <p className="seller-line"><strong>{t("market.seller")}:</strong> {candidate.offer.seller_ref} · {candidate.offer.home_node_code}</p>
        <dl className="stock-facts"><div><dt>{t("market.available")}</dt><dd>{formatAmount(candidate.offer.quantity_available, unitLabel)}</dd></div><div><dt>{t("market.minimum")}</dt><dd>{formatAmount(candidate.offer.minimum_batch, unitLabel)}</dd></div></dl>
        <div className="delivery-line">{isService ? <Wrench size={17} /> : <Truck size={17} />}<span>{isService ? `${t("market.serviceFulfillment")} · ${formatLocalDateTime(quote?.delivery_until ?? candidate.offer.fulfillment_deadline)}` : quote ? `${t("market.deliveryIncluded")} · ${formatLocalDateTime(quote.delivery_until)}` : t("market.deliveryUnavailable")}</span></div>
        <div className="product-price"><span>{t(isService ? "market.serviceTotal" : "market.total")}</span><strong>{formatAmount(candidate.landed_cost, candidate.offer.valuation_unit, sharesUnit)}</strong><small>{formatAmount(candidate.offer.unit_price, candidate.offer.valuation_unit, sharesUnit)} / {unitLabel}</small></div>
        <details className="price-details"><summary>{t("market.costDetails")}</summary><dl><div><dt>{t(isService ? "market.work" : "market.goods")}</dt><dd>{formatAmount(candidate.goods_cost, candidate.offer.valuation_unit, sharesUnit)}</dd></div><div><dt>{t(isService ? "market.noPhysicalDelivery" : "market.logistics")}</dt><dd>{formatAmount(candidate.logistics_cost, candidate.offer.valuation_unit, sharesUnit)}</dd></div><div><dt>{t("market.mandatory")}</dt><dd>{formatAmount(candidate.mandatory_cost, candidate.offer.valuation_unit, sharesUnit)}</dd></div>{Object.entries(quote?.cost_components ?? {}).map(([name, value]) => <div key={name}><dt>{t(`market.costComponent.${name}`, { defaultValue: name })}</dt><dd>{formatAmount(String(value), candidate.offer.valuation_unit, sharesUnit)}</dd></div>)}</dl></details>
        <div className="product-actions"><button className="verify-button" type="button" title={t("market.verify")} onClick={onVerify} disabled={busy}><RefreshCw size={17} /></button>{onQuote ? <button className="quote-button" type="button" onClick={onQuote} disabled={busy}><Truck size={18} />{t("market.quoteDelivery")}</button> : null}<button className="buy-button" type="button" onClick={onBuy} disabled={busy || !canBuy}><ShoppingCart size={18} />{canBuy ? t("market.buy") : t("market.unavailable")}</button></div>
      </div>
    </article>
  );
}

function OrderPanel({ intents, receipts, selectedId, busy, onSelect, onReserve, onCommit, onCancel }: { intents: PurchaseIntent[]; receipts: Array<{ id: string; kind: string; home_node_code: string; status: string; expires_at: string; receipt_hash: string }>; selectedId: string; busy: boolean; onSelect: (id: string) => void; onReserve: (intent: PurchaseIntent, kind: "goods" | "logistics") => void; onCommit: (intent: PurchaseIntent) => void; onCancel: (intent: PurchaseIntent) => void }) {
  const { t } = useTranslation();
  const sharesUnit = t("market.sharesUnit");
  const selectedIntent = intents.find((intent) => intent.id === selectedId);
  const selectedIsService = selectedIntent?.product_code?.startsWith("SERVICE.") ?? false;
  return (
    <div className="orders-layout">
      <section className="market-results">
        <div className="results-heading"><div><h2>{t("market.ordersTitle")}</h2><p>{t("market.ordersTab")}</p></div><span>{intents.length}</span></div>
        {!intents.length ? <div className="empty-market"><ListChecks size={28} /><strong>{t("market.noOrders")}</strong></div> : <div className="order-list">{intents.map((intent) => {
          const breakdown = intent.landed_cost_breakdown as { landed_cost?: string };
          const preset = productPresets.find((item) => item.code === intent.product_code);
          const title = preset ? t(preset.key) : intent.product_code ?? t("market.goods");
          const isService = preset?.kind === "SERVICE" || intent.product_code?.startsWith("SERVICE.");
          const unitLabel = t(`units.${intent.unit_code.toLowerCase()}`, { defaultValue: intent.unit_code });
          return <article className={`order-card ${selectedId === intent.id ? "selected" : ""}`} key={intent.id} onClick={() => onSelect(intent.id)}><div className="order-icon">{isService ? <Wrench size={22} /> : <PackageCheck size={22} />}</div><div className="order-main"><StatusBadge value={intent.status} label={t(isService && intent.status === "GOODS_RESERVED" ? "market.intent.SERVICE_RESERVED" : `market.intent.${intent.status}`)} /><strong>{title}</strong><span>{formatAmount(intent.quantity, unitLabel)} · {intent.delivery_address_text ?? intent.destination_region} · {intent.id.slice(0, 8)}</span></div><div className="order-price"><span>{t("market.orderTotal")}</span><strong>{formatAmount(breakdown.landed_cost ?? intent.max_landed_cost, "COOP", sharesUnit)}</strong><small>{formatLocalDateTime(intent.expires_at)}</small></div><div className="order-actions">{intent.status === "PREPARING" ? <button className="compact-command" disabled={busy} onClick={(event) => { event.stopPropagation(); onReserve(intent, "goods"); }}>{t(isService ? "market.reserveWork" : "market.reserveGoods")}</button> : null}{intent.status === "GOODS_RESERVED" ? <button className="compact-command" disabled={busy} onClick={(event) => { event.stopPropagation(); onReserve(intent, "logistics"); }}>{t(isService ? "market.confirmServiceTime" : "market.reserveDelivery")}</button> : null}{["PREPARED", "COMMITTING"].includes(intent.status) ? <button className="compact-command" disabled={busy} onClick={(event) => { event.stopPropagation(); onCommit(intent); }}>{t("market.confirm")}</button> : null}{["PREPARING", "GOODS_RESERVED", "PREPARED", "CANCELLING"].includes(intent.status) ? <button className="icon-button" title={t("common.cancel")} disabled={busy} onClick={(event) => { event.stopPropagation(); onCancel(intent); }}><XCircle size={17} /></button> : null}</div></article>;
        })}</div>}
      </section>
      {selectedId ? <section className="panel receipt-panel"><div className="panel-heading"><h2>{t("market.signedReservations")}</h2><span>{receipts.length}</span></div><div className="rows">{receipts.map((receipt) => <div className="data-row" key={receipt.id}><strong>{t(selectedIsService ? receipt.kind === "GOODS" ? "market.receiptKind.SERVICE_WORK" : "market.receiptKind.SERVICE_TIME" : `market.receiptKind.${receipt.kind}`)}</strong><span>{receipt.home_node_code}<small>{receipt.receipt_hash.slice(7, 19)}</small></span><StatusBadge value={receipt.status} label={t(`market.receiptStatus.${receipt.status}`)} /><time>{formatLocalDateTime(receipt.expires_at)}</time></div>)}</div></section> : null}
    </div>
  );
}