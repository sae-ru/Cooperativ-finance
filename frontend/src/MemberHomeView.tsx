import {
  ArrowRight,
  BadgeCheck,
  CircleDollarSign,
  CircleHelp,
  Clock3,
  CircleArrowOutUpRight,
  ClipboardList,
  CheckCircle2,
  HandCoins,
  Handshake,
  History,
  Image as ImageIcon,
  Pencil,
  LockKeyhole,
  MapPin,
  PackagePlus,
  Phone,
  Plus,
  RefreshCw,
  Save,
  ShieldCheck,
  Store,
  Trash2,
  UserRound,
  WalletCards,
  Wrench,
  X,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { AdminApiError } from "./api/admin";
import {
  archiveParticipantAddress,
  createParticipantAddress,
  getOfferImage,
  getParticipantAddresses,
  getParticipantDashboard,
  revokeOwnOffer,
  updateParticipantAddress,
  type ParticipantAddress,
  type ParticipantAddressDraft,
  type ParticipantObligation,
  type ParticipantOffer,
  type ParticipantPurchase,
  type ParticipantSale,
} from "./api/participant";
import { userErrorMessage } from "./shared/api-error";
import {
  acceptCompensation,
  getCompensations,
  type CompensationTransfer,
} from "./api/risk";
import { formatLocalDateTime } from "./shared/date-time";
import "./member.css";

function formatAmount(value: string, unit?: string | null): string {
  const number = Number(value);
  const locale = document.documentElement.lang.startsWith("en") ? "en-US" : "ru-RU";
  const formatted = Number.isFinite(number)
    ? new Intl.NumberFormat(locale, { maximumFractionDigits: 4 }).format(number)
    : value;
  return unit ? `${formatted} ${unit}` : formatted;
}
function BalanceValue({ value, unit }: { value: string; unit: string }) {
  return <><span>{formatAmount(value)}</span><small>{unit}</small></>;
}

function remainingAmount(total: string, fulfilled: string, cleared: string): string {
  const remaining = Number(total) - Number(fulfilled) - Number(cleared);
  return Number.isFinite(remaining) ? String(Math.max(0, remaining)) : total;
}

function localizedUnit(code: string, t: (key: string, options?: Record<string, unknown>) => string): string {
  return t(`units.${code.toLowerCase()}`, { defaultValue: code });
}
function offerTitle(offer: ParticipantOffer, t: (key: string) => string): string {
  const value = offer.description.trim();
  return value && !/^[?\s]+$/.test(value)
    ? value
    : t(offer.kind === "SERVICE" ? "member.offers.untitledService" : "member.offers.untitledProduct");
}

function obligationTitle(
  obligation: ParticipantObligation,
  purchases: ParticipantPurchase[],
  sales: ParticipantSale[],
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  const exchange = purchases.find((item) => item.id === obligation.source_purchase_intent_id)
    ?? sales.find((item) => item.id === obligation.source_purchase_intent_id);
  const isValue = ["VALUE", "VALUATION"].includes(obligation.unit_dimension)
    || obligation.subject_type === "MONEY_EQUIVALENT";
  return isValue && exchange
    ? t("member.responsibility.exchangeValue", { item: exchange.description })
    : obligation.description;
}

function errorText(error: unknown): string {
  return userErrorMessage(error);
}

function MemberCompensationSection({ memberId }: { memberId: string }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const compensations = useQuery({
    queryKey: ["risk", "compensations"],
    queryFn: getCompensations,
  });
  const accept = useMutation({
    mutationFn: acceptCompensation,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["risk", "compensations"] }),
        queryClient.invalidateQueries({ queryKey: ["participant-dashboard"] }),
      ]);
    },
  });
  const transfers = compensations.data ?? [];
  if (compensations.isPending || (!compensations.isError && transfers.length === 0)) return null;

  const locale = i18n.resolvedLanguage ?? i18n.language;
  return <section className="member-section member-compensations" aria-labelledby="member-compensations-title">
    <div className="member-section-heading">
      <div>
        <h2 id="member-compensations-title">{t("member.compensation.title")}</h2>
        <p>{t("member.compensation.subtitle")}</p>
      </div>
      <HandCoins size={22} />
    </div>
    {compensations.isError ? <p className="form-error" role="alert">{errorText(compensations.error)}</p> : null}
    <div className="member-compensation-list">
      {transfers.map((transfer) => {
        const isRecipient = transfer.recipient_member_id === memberId;
        const canAccept = isRecipient && transfer.status === "PENDING_ACCEPTANCE";
        const amount = formatAmount(String(transfer.amount), transfer.denomination);
        const directionKey = isRecipient ? "incoming" : "outgoing";
        return <article className={canAccept ? "requires-action" : ""} key={transfer.id}>
          <div className="member-compensation-icon">
            {transfer.status === "SETTLED" ? <CheckCircle2 size={23} /> : <HandCoins size={23} />}
          </div>
          <div className="member-compensation-main">
            <span>{t(`member.compensation.${directionKey}`)}</span>
            <strong>{amount}</strong>
            <p>{t("member.compensation.explanation")}</p>
            <small>{t("member.compensation.authorizedAt")} {formatLocalDateTime(transfer.authorized_at)}</small>
          </div>
          <div className="member-compensation-action">
            <span className={`status ${transfer.status === "SETTLED" ? "good" : transfer.status === "VOIDED" ? "bad" : "warn"}`}>
              {t(`risk.compensation.status.${transfer.status}`)}
            </span>
            {canAccept ? <button
              className="member-primary-command"
              type="button"
              disabled={accept.isPending}
              onClick={() => {
                if (window.confirm(t("member.compensation.confirm", { amount }))) {
                  accept.mutate(transfer as CompensationTransfer);
                }
              }}
            >
              <CheckCircle2 size={17} />
              {accept.isPending ? t("member.compensation.accepting") : t("risk.compensation.accept")}
            </button> : null}
          </div>
        </article>;
      })}
    </div>
    {accept.isError ? <p className="form-error" role="alert">{userErrorMessage(accept.error, locale)}</p> : null}
  </section>;
}

function OfferImage({ offer, title }: { offer: ParticipantOffer; title: string }) {
  const { t } = useTranslation();
  const image = useQuery({
    queryKey: ["offer-image", offer.record_id],
    queryFn: () => getOfferImage(offer.record_id),
    enabled: offer.has_image,
    staleTime: 60 * 60_000,
  });
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!image.data) return;
    const next = URL.createObjectURL(image.data);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [image.data]);
  if (url) return <img src={url} alt={title} onError={() => setUrl(null)} />;
  return <div className={`member-offer-placeholder ${offer.kind.toLowerCase()}`} aria-label={t(offer.kind === "SERVICE" ? "market.service" : "market.product")}>
    {offer.kind === "SERVICE" ? <Wrench size={30} /> : <ImageIcon size={30} />}
  </div>;
}

function AddressBookSection({ cooperativeId, contactName }: { cooperativeId: string; contactName: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const addressBook = useQuery({
    queryKey: ["participant-addresses"],
    queryFn: getParticipantAddresses,
  });
  const [editing, setEditing] = useState<ParticipantAddress | null>(null);
  const [draft, setDraft] = useState<ParticipantAddressDraft | null>(null);

  const save = useMutation({
    mutationFn: ({ address, values }: { address: ParticipantAddress | null; values: ParticipantAddressDraft }) =>
      address ? updateParticipantAddress(address, values) : createParticipantAddress(values),
    onSuccess: async () => {
      setEditing(null);
      setDraft(null);
      await queryClient.invalidateQueries({ queryKey: ["participant-addresses"] });
    },
  });
  const archive = useMutation({
    mutationFn: archiveParticipantAddress,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["participant-addresses"] }),
  });

  function newAddress() {
    setEditing(null);
    setDraft({
      cooperative_id: cooperativeId,
      label: "",
      purpose: "BOTH",
      region_code: "",
      address_text: "",
      contact_name: contactName,
      contact_phone: "",
      instructions: "",
      is_default_pickup: false,
      is_default_delivery: false,
    });
    save.reset();
  }

  function editAddress(address: ParticipantAddress) {
    setEditing(address);
    setDraft({
      cooperative_id: address.cooperative_id,
      label: address.label,
      purpose: address.purpose,
      region_code: address.region_code,
      address_text: address.address_text,
      contact_name: address.contact_name,
      contact_phone: address.contact_phone,
      instructions: address.instructions,
      is_default_pickup: address.is_default_pickup,
      is_default_delivery: address.is_default_delivery,
    });
    save.reset();
  }

  function change(values: Partial<ParticipantAddressDraft>) {
    setDraft((current) => current ? { ...current, ...values } : current);
  }

  function changePurpose(purpose: ParticipantAddressDraft["purpose"]) {
    setDraft((current) => current ? {
      ...current,
      purpose,
      is_default_pickup: ["PICKUP", "BOTH"].includes(purpose) ? current.is_default_pickup : false,
      is_default_delivery: ["DELIVERY", "BOTH"].includes(purpose) ? current.is_default_delivery : false,
    } : current);
  }

  const addresses = addressBook.data ?? [];
  return <section className="member-section member-address-book" aria-labelledby="member-addresses-title">
    <div className="member-section-heading"><div><h2 id="member-addresses-title">{t("member.addresses.title")}</h2><p>{t("member.addresses.subtitle")}</p></div><button className="member-primary-command" type="button" onClick={newAddress}><Plus size={18} />{t("member.addresses.add")}</button></div>
    {addressBook.isPending ? <div className="state"><RefreshCw className="spin" size={20} />{t("common.loading")}</div> : null}
    {addressBook.isError ? <p className="form-error" role="alert">{errorText(addressBook.error)}</p> : null}
    {!addressBook.isPending && !addresses.length && !draft ? <div className="member-empty compact"><MapPin size={22} /><span>{t("member.addresses.empty")}</span><button type="button" onClick={newAddress}>{t("member.addresses.addFirst")}</button></div> : null}
    {addresses.length ? <div className="member-address-list">{addresses.map((address) => <article key={address.id}>
      <div className="member-address-icon"><MapPin size={21} /></div>
      <div className="member-address-main"><div><strong data-i18n-ignore="true">{address.label}</strong><span>{t(`member.addresses.purpose.${address.purpose}`)}</span></div><p data-i18n-ignore="true">{address.address_text}</p><small data-i18n-ignore="true">{address.region_code}</small><small data-i18n-ignore="true"><Phone size={13} />{address.contact_name} · {address.contact_phone}</small>{address.instructions ? <small data-i18n-ignore="true">{address.instructions}</small> : null}<div className="member-address-defaults">{address.is_default_pickup ? <span>{t("member.addresses.defaultPickup")}</span> : null}{address.is_default_delivery ? <span>{t("member.addresses.defaultDelivery")}</span> : null}</div></div>
      <div className="member-address-actions"><button className="icon-button" type="button" title={t("member.addresses.edit")} onClick={() => editAddress(address)}><Pencil size={17} /></button><button className="icon-button" type="button" title={t("member.addresses.archive")} disabled={archive.isPending} onClick={() => { if (window.confirm(t("member.addresses.archiveConfirm", { label: address.label }))) archive.mutate(address); }}><Trash2 size={17} /></button></div>
    </article>)}</div> : null}
    {draft ? <form className="member-address-form" onSubmit={(event) => { event.preventDefault(); save.mutate({ address: editing, values: draft }); }}>
      <div className="member-address-form-heading"><strong>{t(editing ? "member.addresses.editTitle" : "member.addresses.addTitle")}</strong><button className="icon-button" type="button" title={t("common.cancel")} onClick={() => { setEditing(null); setDraft(null); }}><X size={18} /></button></div>
      <label><span>{t("member.addresses.label")}</span><input value={draft.label} onChange={(event) => change({ label: event.target.value })} placeholder={t("member.addresses.labelPlaceholder")} required minLength={2} /></label>
      <label><span>{t("member.addresses.purposeLabel")}</span><select value={draft.purpose} onChange={(event) => changePurpose(event.target.value as ParticipantAddressDraft["purpose"])}><option value="BOTH">{t("member.addresses.purpose.BOTH")}</option><option value="PICKUP">{t("member.addresses.purpose.PICKUP")}</option><option value="DELIVERY">{t("member.addresses.purpose.DELIVERY")}</option></select></label>
      <label><span>{t("member.addresses.region")}</span><input value={draft.region_code} onChange={(event) => change({ region_code: event.target.value.toUpperCase() })} placeholder={t("member.addresses.regionPlaceholder")} pattern="[A-Za-z0-9][A-Za-z0-9._-]{1,62}" required /></label>
      <label className="span-two"><span>{t("member.addresses.exactAddress")}</span><input value={draft.address_text} onChange={(event) => change({ address_text: event.target.value })} placeholder={t("member.addresses.addressPlaceholder")} required minLength={5} /></label>
      <label><span>{t("member.addresses.contact")}</span><input value={draft.contact_name} onChange={(event) => change({ contact_name: event.target.value })} required minLength={2} /></label>
      <label><span>{t("member.addresses.phone")}</span><input type="tel" value={draft.contact_phone} onChange={(event) => change({ contact_phone: event.target.value })} placeholder={t("market.contactPhonePlaceholder")} required minLength={5} /></label>
      <label className="span-two"><span>{t("member.addresses.instructions")}</span><textarea value={draft.instructions ?? ""} onChange={(event) => change({ instructions: event.target.value })} placeholder={t("member.addresses.instructionsPlaceholder")} rows={3} /></label>
      <div className="member-address-default-controls span-two"><label><input type="checkbox" checked={draft.is_default_pickup} disabled={draft.purpose === "DELIVERY"} onChange={(event) => change({ is_default_pickup: event.target.checked })} />{t("member.addresses.makeDefaultPickup")}</label><label><input type="checkbox" checked={draft.is_default_delivery} disabled={draft.purpose === "PICKUP"} onChange={(event) => change({ is_default_delivery: event.target.checked })} />{t("member.addresses.makeDefaultDelivery")}</label></div>
      {save.isError ? <p className="form-error span-two" role="alert">{errorText(save.error)}</p> : null}
      {archive.isError ? <p className="form-error span-two" role="alert">{errorText(archive.error)}</p> : null}
      <div className="member-address-form-actions span-two"><button className="secondary-button" type="button" onClick={() => { setEditing(null); setDraft(null); }}>{t("common.cancel")}</button><button className="member-primary-command" type="submit" disabled={save.isPending}>{save.isPending ? <RefreshCw className="spin" size={17} /> : <Save size={17} />}{t("common.save")}</button></div>
    </form> : null}
    <p className="member-section-foot">{t("member.addresses.privacy")}</p>
  </section>;
}

export default function MemberHomeView({ onNavigate }: { onNavigate: (view: "discovery" | "exchange", section?: "search" | "sell" | "intents") => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const dashboard = useQuery({ queryKey: ["participant-dashboard"], queryFn: getParticipantDashboard });
  const revoke = useMutation({
    mutationFn: (offer: ParticipantOffer) => revokeOwnOffer(offer, t("member.offers.revokeReason")),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["participant-dashboard"] }),
        queryClient.invalidateQueries({ queryKey: ["federated-search"] }),
      ]);
    },
  });

  if (dashboard.isPending) return <div className="view-stack"><div className="state"><RefreshCw className="spin" size={24} />{t("common.loading")}</div></div>;
  if (dashboard.isError) return <div className="view-stack"><div className="state error">{errorText(dashboard.error)}</div></div>;

  const data = dashboard.data;
  const cooperative = data.memberships.find((item) => item.membership_status === "ACTIVE") ?? data.memberships[0];
  const sharesUnit = data.shares.denomination ?? t("market.sharesUnit");
  const activeOffers = data.offers.filter((item) => item.status === "ACTIVE");
  const activeObligations = data.obligations.filter((item) => !["FULFILLED", "CLOSED"].includes(item.status));
  const sourceCount = data.shares.accounts.reduce((total, account) => total + account.sources.length, 0);
  const contourOrder = ["PRIMARY", "GUARANTEE", "ROLE", "INFRASTRUCTURE", "SOLIDARITY"];
  const accounts = [...data.shares.accounts].sort(
    (a, b) => contourOrder.indexOf(a.contour) - contourOrder.indexOf(b.contour),
  );

  return <div className="view-stack member-home-view">
    <header className="member-heading">
      <div className="member-avatar"><UserRound size={28} /></div>
      <div><span className="eyebrow">{t("member.eyebrow")}</span><h1>{data.profile.display_name}</h1><p>{cooperative ? `${cooperative.cooperative_name} · ${t("member.number")} ${cooperative.member_number}` : t("member.noCooperative")}</p></div>
      <span className={`status ${data.profile.member_status === "ACTIVE" ? "good" : "warn"}`}><BadgeCheck size={15} />{t(`member.status.${data.profile.member_status}`)}</span>
    </header>

    <section className="member-balance-band" aria-labelledby="member-balance-title">
      <div className="member-balance-primary"><span id="member-balance-title">{t("member.shares.available")}</span><strong><BalanceValue value={data.shares.available} unit={sharesUnit} /></strong><small>{t("member.shares.availableHint")}</small></div>
      <dl>
        <div><dt><WalletCards size={18} />{t("member.shares.total")}</dt><dd><BalanceValue value={data.shares.total_balance} unit={sharesUnit} /></dd></div>
        <div><dt><LockKeyhole size={18} />{t("member.shares.reserved")}</dt><dd><BalanceValue value={data.shares.reserved} unit={sharesUnit} /></dd></div>
        <div><dt><ShieldCheck size={18} />{t("member.shares.protected")}</dt><dd><BalanceValue value={data.shares.protected} unit={sharesUnit} /></dd></div>
        <div><dt><CircleDollarSign size={18} />{t("member.earned")}</dt><dd><BalanceValue value={data.exchange_position.earned_settled} unit={sharesUnit} /></dd></div>
        <div><dt><Clock3 size={18} />{t("member.expected")}</dt><dd><BalanceValue value={data.exchange_position.expected_incoming} unit={sharesUnit} /></dd></div>
        <div><dt><CircleArrowOutUpRight size={18} />{t("member.due")}</dt><dd><BalanceValue value={data.exchange_position.expected_outgoing} unit={sharesUnit} /></dd></div>
      </dl>
    </section>

    <MemberCompensationSection memberId={data.profile.member_id} />

    {data.shares.account_missing ? <section className="member-notice" role="status"><CircleHelp size={23} /><div><strong>{t("member.shares.noAccount")}</strong><p>{t("member.shares.noAccountHelp")}</p></div></section> : null}

    <section className="member-actions" aria-label={t("member.actions.title")}>
      <button onClick={() => onNavigate("discovery", "sell")}><PackagePlus size={21} /><span><strong>{t("member.actions.offer")}</strong><small>{t("member.actions.offerHint")}</small></span><ArrowRight size={18} /></button>
      <button onClick={() => onNavigate("discovery", "search")}><Store size={21} /><span><strong>{t("member.actions.exchange")}</strong><small>{t("member.actions.exchangeHint")}</small></span><ArrowRight size={18} /></button>
      <button onClick={() => onNavigate("exchange")}><Handshake size={21} /><span><strong>{t("member.actions.obligations")}</strong><small>{t("member.actions.active", { count: activeObligations.length })}</small></span><ArrowRight size={18} /></button>
    </section>

    {cooperative ? <AddressBookSection cooperativeId={cooperative.cooperative_id} contactName={data.profile.display_name} /> : null}

    <section className="member-section" aria-labelledby="member-offers-title">
      <div className="member-section-heading"><div><h2 id="member-offers-title">{t("member.offers.title")}</h2><p>{t("member.offers.subtitle")}</p></div><button className="member-primary-command" onClick={() => onNavigate("discovery", "sell")}><PackagePlus size={18} />{t("member.offers.add")}</button></div>
      {!data.offers.length ? <div className="member-empty"><Store size={25} /><strong>{t("member.offers.empty")}</strong><button onClick={() => onNavigate("discovery", "sell")}>{t("member.offers.addFirst")}</button></div> : <div className="member-offer-grid">{data.offers.map((offer) => { const title = offerTitle(offer, t); return <article className={offer.status !== "ACTIVE" ? "inactive" : ""} key={offer.record_id}>
        <OfferImage offer={offer} title={title} />
        <div className="member-offer-body"><div className="member-offer-top"><span>{t(offer.kind === "SERVICE" ? "market.service" : "market.product")}</span><span className={`status ${offer.status === "ACTIVE" ? "good" : "warn"}`}>{t(`member.offers.status.${offer.status}`)}</span></div><h3>{title}</h3><p>{formatAmount(offer.quantity_available, t(`units.${offer.unit_code.toLowerCase()}`, { defaultValue: offer.unit_code }))}</p><div className="member-offer-price"><strong>{formatAmount(offer.unit_price, t("market.sharesUnit"))}</strong><span>/ {t(`units.${offer.unit_code.toLowerCase()}`, { defaultValue: offer.unit_code })}</span></div>{offer.pickup_address_text ? <small className="member-offer-address"><MapPin size={14} />{t("member.offers.pickupAt")} {offer.pickup_address_text}</small> : null}<small>{t("member.offers.until")} {formatLocalDateTime(offer.availability_until)}</small>{offer.status === "ACTIVE" ? <button className="icon-button member-offer-remove" title={t("member.offers.revoke")} onClick={() => revoke.mutate(offer)} disabled={revoke.isPending}><Trash2 size={17} /></button> : null}</div>
      </article>; })}</div>}
      {revoke.isError ? <p className="form-error">{errorText(revoke.error)}</p> : null}
      <p className="member-section-foot">{t("member.offers.activeCount", { count: activeOffers.length })}</p>
    </section>

    <div className="member-two-columns member-activity-grid">
      <section className="member-section" aria-labelledby="member-purchases-title">
        <div className="member-section-heading"><div><h2 id="member-purchases-title">{t("member.activity.purchases")}</h2><p>{t("member.activity.purchasesHint")}</p></div><History size={22} /></div>
        {!data.purchases.length ? <div className="member-empty compact"><History size={22} /><span>{t("member.activity.noPurchases")}</span></div> : <div className="member-history-list">{data.purchases.slice(0, 6).map((item) => <article key={item.id}><div><span className="member-history-kind">{t("member.activity.received")}</span><strong>{item.description}</strong><small>{formatAmount(item.quantity, localizedUnit(item.unit_code, t))}</small></div><div><span className={`status ${item.status === "COMMITTED" ? "good" : "warn"}`}>{t(`member.activity.status.${item.status}`)}</span><b>{formatAmount(item.landed_cost, sharesUnit)}</b><small>{formatLocalDateTime(item.committed_at ?? item.created_at)}</small></div></article>)}</div>}
      </section>

      <section className="member-section" aria-labelledby="member-sales-title">
        <div className="member-section-heading"><div><h2 id="member-sales-title">{t("member.activity.sales")}</h2><p>{t("member.activity.salesHint")}</p></div><Store size={22} /></div>
        {!data.sales.length ? <div className="member-empty compact"><Store size={22} /><span>{t("member.activity.noSales")}</span></div> : <div className="member-history-list">{data.sales.slice(0, 6).map((item) => <article key={item.id}><div><span className="member-history-kind">{t("member.activity.ordered")}</span><strong>{item.description}</strong><small>{formatAmount(item.quantity, localizedUnit(item.unit_code, t))}</small>{item.delivery_address_text ? <small className="member-history-address"><MapPin size={13} />{t("member.activity.deliverTo")} {item.delivery_address_text}</small> : null}</div><div><span className={`status ${item.status === "COMMITTED" ? "good" : "warn"}`}>{t(`member.activity.status.${item.status}`)}</span><b>{formatAmount(item.goods_value, sharesUnit)}</b><small>{formatLocalDateTime(item.committed_at ?? item.created_at)}</small></div></article>)}</div>}
      </section>
    </div>

    <section className="member-section" aria-labelledby="member-responsibility-title">
      <div className="member-section-heading"><div><h2 id="member-responsibility-title">{t("member.responsibility.title")}</h2><p>{t("member.responsibility.subtitle")}</p></div><ClipboardList size={22} /></div>
      {!activeObligations.length && !data.commitments.length ? <div className="member-empty compact"><BadgeCheck size={22} /><span>{t("member.responsibility.empty")}</span></div> : <div className="member-responsibility-list">
        {activeObligations.slice(0, 8).map((obligation) => <article key={obligation.id}><div className="member-responsibility-heading"><span className={obligation.direction === "OWE" ? "owe" : "receive"}>{t(`member.responsibility.direction.${obligation.direction}`)}</span><span className="status warn">{t(`member.responsibility.status.${obligation.status}`)}</span></div><strong>{obligationTitle(obligation, data.purchases, data.sales, t)}</strong><dl><div><dt>{t("member.responsibility.remaining")}</dt><dd>{formatAmount(remainingAmount(obligation.quantity_total, obligation.quantity_fulfilled, obligation.quantity_cleared), localizedUnit(obligation.unit_code, t))}</dd></div><div><dt>{t("member.responsibility.dueAt")}</dt><dd>{formatLocalDateTime(obligation.due_at)}</dd></div></dl>{obligation.clearing_allowed ? <small>{t("member.responsibility.clearingAllowed")}</small> : null}</article>)}
        {data.commitments.map((commitment) => <article key={commitment.id}><div className="member-responsibility-heading"><span className="owe">{t(`member.responsibility.type.${commitment.type}`)}</span><span className="status warn">{t(`member.responsibility.commitmentStatus.${commitment.status}`)}</span></div><strong>{t("member.responsibility.shareExposure")}</strong><dl><div><dt>{t("member.responsibility.reserved")}</dt><dd>{formatAmount(commitment.amount_reserved, sharesUnit)}</dd></div><div><dt>{t("member.responsibility.expiresAt")}</dt><dd>{formatLocalDateTime(commitment.expires_at)}</dd></div></dl></article>)}
      </div>}
    </section>
    <div className="member-two-columns">
      <section className="member-section" aria-labelledby="share-contours-title">
        <div className="member-section-heading"><div><h2 id="share-contours-title">{t("member.shares.contours")}</h2><p>{t("member.shares.contoursHint")}</p></div><WalletCards size={22} /></div>
        {!accounts.length ? <div className="member-empty compact"><WalletCards size={22} /><span>{t("member.shares.noContours")}</span></div> : <div className="member-account-list">{accounts.map((account) => <details key={account.id} open={accounts.length === 1}><summary><span><strong>{t(`member.contour.${account.contour}`)}</strong><small>{account.denomination}</small></span><b>{formatAmount(account.balance, account.denomination)}</b></summary><dl><div><dt>{t("member.shares.available")}</dt><dd>{formatAmount(account.available, account.denomination)}</dd></div><div><dt>{t("member.shares.protected")}</dt><dd>{formatAmount(account.protected, account.denomination)}</dd></div><div><dt>{t("member.shares.reserved")}</dt><dd>{formatAmount(account.reserved, account.denomination)}</dd></div><div><dt>{t("member.shares.policy")}</dt><dd>{account.policy ? `v${account.policy.version} · ${account.policy.terms_hash.slice(7, 19)}` : "—"}</dd></div><div><dt>{t("member.shares.maxExposure")}</dt><dd>{account.policy ? formatAmount(account.policy.max_member_exposure, account.denomination) : "—"}</dd></div></dl>{account.sources.map((source) => <p key={source.event_id}><strong>{t("member.shares.source")}</strong> {source.source_reference} · {formatAmount(source.amount, account.denomination)} · {formatLocalDateTime(source.created_at)}</p>)}</details>)}</div>}
      </section>

      <section className="member-section" aria-labelledby="profile-title">
        <div className="member-section-heading"><div><h2 id="profile-title">{t("member.profile.title")}</h2><p>{t("member.profile.subtitle")}</p></div><UserRound size={22} /></div>
        <dl className="member-profile-list"><div><dt>{t("member.profile.login")}</dt><dd>{data.profile.login}</dd></div><div><dt>{t("member.profile.memberId")}</dt><dd>{data.profile.member_id.slice(0, 8)}</dd></div><div><dt>{t("member.profile.cooperative")}</dt><dd>{cooperative?.cooperative_name ?? "—"}</dd></div><div><dt>{t("member.profile.cooperativeCode")}</dt><dd>{cooperative?.cooperative_code ?? "—"}</dd></div><div><dt>{t("member.profile.memberNumber")}</dt><dd>{cooperative?.member_number ?? "—"}</dd></div><div><dt>{t("member.profile.membershipStatus")}</dt><dd>{cooperative ? t(`member.membershipStatus.${cooperative.membership_status}`, { defaultValue: cooperative.membership_status }) : "—"}</dd></div><div><dt>{t("member.profile.joined")}</dt><dd>{formatLocalDateTime(cooperative?.joined_at ?? data.profile.member_since)}</dd></div><div><dt>{t("member.profile.lastLogin")}</dt><dd>{data.profile.last_login_at ? formatLocalDateTime(data.profile.last_login_at) : t("member.profile.firstLogin")}</dd></div></dl>
      </section>
    </div>

    <section className="member-section member-valuation" aria-labelledby="valuation-title">
      <div className="member-section-heading"><div><h2 id="valuation-title">{t("member.valuation.title")}</h2><p>{t("member.valuation.subtitle")}</p></div><CircleHelp size={22} /></div>
      <div className="valuation-explanation"><p><strong>{t("member.valuation.shareCapital")}</strong>{t("member.valuation.shareCapitalText")}</p><p><strong>{t("member.valuation.exchangeValue")}</strong>{t("member.valuation.exchangeValueText")}</p><p><strong>{t("member.valuation.sources")}</strong>{t("member.valuation.sourcesText", { count: sourceCount })}</p></div>
    </section>
  </div>;
}
