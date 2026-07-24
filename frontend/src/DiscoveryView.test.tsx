import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DiscoveryView from "./DiscoveryView";
import i18n from "./i18n";
import type { Principal } from "./api/admin";
import * as discovery from "./api/discovery";
import * as inventory from "./api/inventory";
import * as participant from "./api/participant";

vi.mock("./api/discovery", async () => {
  const actual = await vi.importActual<typeof import("./api/discovery")>("./api/discovery");
  return Object.fromEntries(
    Object.entries(actual).map(([key, value]) => [
      key,
      typeof value === "function" ? vi.fn() : value,
    ]),
  );
});
vi.mock("./api/inventory", () => ({ uploadEvidence: vi.fn() }));
vi.mock("./api/participant", async () => {
  const actual = await vi.importActual<typeof import("./api/participant")>("./api/participant");
  return Object.fromEntries(
    Object.entries(actual).map(([key, value]) => [
      key,
      typeof value === "function" ? vi.fn() : value,
    ]),
  );
});


const candidate: discovery.SearchCandidate = {
  offer: {
    record_id: "offer-record-1",
    offer_id: "offer-1",
    offer_version: 1,
    home_node_code: "peer-west-01",
    seller_ref: "PEER-FARM-17",
    product_code: "CABBAGE.WHITE",
    description: "Свежая белокочанная капуста",
    quality_grade: "A",
    certificate_refs: ["quality:demo-v1"],
    quantity_available: "1200.000",
    quantity_is_band: false,
    unit_code: "KG",
    unit_scale: 3,
    minimum_batch: "10.000",
    divisible: true,
    origin_region: "WEST-DISTRICT",
    origin_precision: "REGION",
    availability_from: "2026-07-21T10:00:00Z",
    availability_until: "2026-07-27T10:00:00Z",
    fulfillment_deadline: "2026-07-28T10:00:00Z",
    unit_price: "2.85",
    mandatory_fee_per_unit: "0.05",
    valuation_unit: "COOP",
    price_policy_version: "DEMO-V1",
    handling_requirements: {},
    counterparty_policy: {},
    geography_policy: {},
    guarantee_terms: {},
    source_mode: "INDEXED",
    node_sequence: 1,
    signed_at: "2026-07-21T10:00:00Z",
    valid_until: "2026-07-28T10:00:00Z",
    signer_fingerprint: "sha256:1111111111111111111111111111111111111111111111111111111111111111",
    payload_hash: "sha256:2222222222222222222222222222222222222222222222222222222222222222",
  },
  quote: {
    record_id: "quote-record-1",
    offer_record_id: "offer-record-1",
    origin_region: "WEST-DISTRICT",
    quote_id: "quote-1",
    quote_version: 1,
    home_node_code: "node-local-01",
    carrier_ref: "LOCAL-CARRIER-01",
    destination_region: "EAST-DISTRICT",
    route_legs: [],
    custody_transfers: 1,
    capacity: "1200.000",
    unit_code: "KG",
    cost_components: { transport: "32.00", handling: "3.00", insurance: "1.00" },
    valuation_unit: "COOP",
    cost_status: "CONFIRMED",
    delivery_from: "2026-07-21T14:00:00Z",
    delivery_until: "2026-07-23T10:00:00Z",
    liability_limit: "5000.00",
    bond_ref: "DEMO-LOGISTICS-BOND",
    assumptions: [],
    signed_at: "2026-07-21T10:00:00Z",
    valid_until: "2026-07-28T10:00:00Z",
    signer_fingerprint: "sha256:3333333333333333333333333333333333333333333333333333333333333333",
  },
  freshness: "SIGNED_CACHED",
  signature_verified: true,
  goods_cost: "28.50",
  logistics_cost: "35.00",
  mandatory_cost: "1.50",
  landed_cost: "65.00",
  cost_status: "CONFIRMED",
};

const principal: Principal = {
  user_id: "user-1",
  login: "registrar",
  member_id: "member-1",
  must_change_password: false,
  roles: [],
};

function renderView(activePrincipal = principal) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DiscoveryView principal={activePrincipal} />
    </QueryClientProvider>,
  );
}

describe("DiscoveryView", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en");
    vi.clearAllMocks();
    vi.mocked(inventory.uploadEvidence).mockResolvedValue("evidence-image-1");
    vi.mocked(participant.getParticipantAddresses).mockResolvedValue([]);
    vi.mocked(discovery.getPurchaseIntents).mockResolvedValue([]);
    vi.mocked(discovery.getMyLogisticsQuotes).mockResolvedValue([]);
    vi.mocked(discovery.getReservationReceipts).mockResolvedValue([]);
    vi.mocked(discovery.searchCatalog).mockResolvedValue({
      data: [candidate],
      mode: "DIRECT",
      peer_statuses: [
        {
          node_code: "peer-west-01",
          status: "SUCCEEDED",
          result_code: "OK",
          imported_offers: 1,
          imported_quotes: 1,
        },
      ],
      ranking_version: "LANDED_COST_V1",
      request_id: "request-1",
    });
    vi.mocked(discovery.verifyOffer).mockResolvedValue({
      valid: true,
      freshness: "SIGNED_CACHED",
      home_node_code: "peer-west-01",
      signer_fingerprint: candidate.offer.signer_fingerprint,
      valid_until: candidate.offer.valid_until,
    });
    vi.mocked(discovery.createPurchaseIntent).mockResolvedValue({
      event_id: "event-1",
      object_id: "intent-1",
      replayed: false,
    });vi.mocked(discovery.publishOffer).mockResolvedValue({
      event_id: "event-offer",
      object_id: "offer-1",
      replayed: false,
    });
    vi.mocked(discovery.publishLogisticsQuote).mockResolvedValue({
      event_id: "event-quote",
      object_id: "quote-new",
      replayed: false,
    });
    vi.mocked(discovery.commitPurchase).mockResolvedValue({
      event_id: "event-commit",
      object_id: "intent-commit",
      replayed: false,
    });
    vi.mocked(discovery.cancelPurchase).mockResolvedValue({
      event_id: "event-cancel",
      object_id: "intent-cancel",
      replayed: false,
    });
  });

  it("searches, exposes the landed-cost formula, and starts purchase preparation", async () => {
    const user = userEvent.setup();
    renderView();

    expect(screen.getByRole("heading", { name: "What do you need?" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Cabbage", level: 3 })).toBeInTheDocument();
    expect(screen.getByText("Signed snapshot")).toBeInTheDocument();
    await user.click(screen.getByText("Share breakdown"));
    expect(screen.getByText("Transport")).toBeInTheDocument();
    expect(screen.getByText("65 shares")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Get for shares" }));
    expect(screen.getByRole("heading", { name: "Where should it be delivered?" })).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: /^Exact delivery address/ }), "12 Farm Road, Barn 2");
    await user.type(screen.getByRole("textbox", { name: "Person receiving the cargo" }), "John Buyer");
    await user.type(screen.getByRole("textbox", { name: "Contact phone" }), "+1 555 010 2000");
    await user.type(screen.getByRole("textbox", { name: "Handover instructions" }), "Call at the gate");
    await user.click(screen.getByRole("button", { name: "Continue with this address" }));
    await waitFor(() => expect(discovery.createPurchaseIntent).toHaveBeenCalledWith(
      candidate,
      "10.000",
      {
        address_text: "12 Farm Road, Barn 2",
        contact_name: "John Buyer",
        contact_phone: "+1 555 010 2000",
        instructions: "Call at the gate",
      },
    ));
  });

  it("reuses a saved delivery point and copies it into the order", async () => {
    const user = userEvent.setup();
    const home: participant.ParticipantAddress = {
      id: "address-home",
      cooperative_id: "coop-1",
      label: "Home",
      purpose: "DELIVERY",
      region_code: "EAST-DISTRICT",
      address_text: "14 Home Road",
      contact_name: "John Buyer",
      contact_phone: "+1 555 010 2000",
      instructions: "Call at the gate",
      is_default_pickup: false,
      is_default_delivery: true,
      status: "ACTIVE",
      created_at: "2026-07-24T10:00:00Z",
      updated_at: "2026-07-24T10:00:00Z",
      version: 1,
    };
    vi.mocked(participant.getParticipantAddresses).mockResolvedValue([home]);

    renderView();

    expect(await screen.findByRole("heading", { name: "Cabbage", level: 3 })).toBeInTheDocument();
    expect(await screen.findByText("14 Home Road")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Get for shares" }));
    expect(screen.getByRole("combobox", { name: /^Saved delivery point/ })).toHaveValue(home.id);
    expect(screen.getByRole("textbox", { name: /^Exact delivery address/ })).toHaveValue("14 Home Road");
    await user.click(screen.getByRole("button", { name: "Continue with this address" }));

    await waitFor(() => expect(discovery.createPurchaseIntent).toHaveBeenCalledWith(
      candidate,
      "10.000",
      {
        address_text: home.address_text,
        contact_name: home.contact_name,
        contact_phone: home.contact_phone,
        instructions: home.instructions,
      },
    ));
  });

  it("lets a logistics provider publish a delivery quote for a found offer", async () => {
    const user = userEvent.setup();
    vi.mocked(discovery.getMyLogisticsQuotes).mockResolvedValue([{
      ...candidate.quote!,
      capacity: "10.000000000000",
    }]);
    renderView({
      ...principal,
      login: "carrier",
      member_id: "carrier-member-1",
      roles: [{ assignment_id: "role-logistics", role: "LOGISTICS_OPERATOR", cooperative_id: "30000000-0000-0000-0000-000000000001" }],
    });

    expect(await screen.findByRole("heading", { name: "Cabbage", level: 3 })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Quote delivery" }));
    expect(screen.getByRole("heading", { name: "Offer delivery" })).toBeInTheDocument();
    expect(screen.getByText("WEST-DISTRICT")).toBeInTheDocument();
    expect(await screen.findByText("10 kg")).toBeInTheDocument();
    const destination = screen.getByRole("textbox", { name: "Delivery district or town" });
    await user.clear(destination);
    await user.type(destination, "NORTH-DISTRICT");
    await user.click(screen.getByRole("button", { name: "Publish delivery" }));

    await waitFor(() => expect(discovery.publishLogisticsQuote).toHaveBeenCalledWith(
      expect.objectContaining({
        offer_record_id: "offer-record-1",
        destination_region: "NORTH-DISTRICT",
        capacity: "10.000",
        transport_cost: "8.00",
        handling_cost: "1.00",
      }),
      "carrier",
    ));
    expect(screen.getByText("Delivery is published. Buyers can now see the full valuation.")).toBeInTheDocument();
  });

  it("publishes a simple signed offer for the responsible catalog operator", async () => {
    const user = userEvent.setup();
    renderView({
      ...principal,
      member_id: "seller-member-1",
      roles: [{ assignment_id: "role-1", role: "NODE_BUSINESS_OPERATOR", cooperative_id: "30000000-0000-0000-0000-000000000001" }],
    });

    await user.click(screen.getByRole("tab", { name: "Offer" }));
    expect(screen.getByRole("heading", { name: "Offer goods" })).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: /Exact (pickup|service) address/ }), "12 Farm Road, Barn 2");
    await user.type(screen.getByRole("textbox", { name: "Contact phone" }), "+1 555 010 2000");
    await user.click(screen.getByRole("button", { name: "Publish offer" }));

    await waitFor(() => expect(discovery.publishOffer).toHaveBeenCalled());
    expect(vi.mocked(discovery.publishOffer).mock.calls[0]?.[1]).toBe("seller-member-1");
    expect(vi.mocked(discovery.publishOffer).mock.calls[0]?.[0]).toMatchObject({
      product_code: "MILK.UHT.3_2",
      unit_code: "L",
      quantity_available: "100.000",
    });
    expect(screen.getByText("The offer is published and available to other nodes.")).toBeInTheDocument();
  });
  it("finds computer repair by its human-readable preset and uses hours", async () => {
    const user = userEvent.setup();
    renderView();

    await user.click(screen.getByRole("button", { name: "Computer repair" }));

    await waitFor(() => expect(discovery.searchCatalog).toHaveBeenLastCalledWith(
      expect.objectContaining({
        product_code: "SERVICE.COMPUTER.REPAIR",
        unit_code: "HOUR",
      }),
    ));
    expect(screen.getByRole("combobox", { name: "Unit" })).toHaveValue("HOUR");
  });
  it("publishes computer repair with a stable searchable service code", async () => {
    const user = userEvent.setup();
    renderView({
      ...principal,
      member_id: "service-member-1",
      roles: [{ assignment_id: "role-participant", role: "EXCHANGE_PARTICIPANT", cooperative_id: "30000000-0000-0000-0000-000000000001" }],
    });

    await user.click(screen.getByRole("tab", { name: "Offer" }));
    await user.click(screen.getByRole("button", { name: /^Service$/ }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Service type" }), "SERVICE.COMPUTER.REPAIR");
    await user.type(screen.getByRole("textbox", { name: /Exact (pickup|service) address/ }), "12 Farm Road, Barn 2");
    await user.type(screen.getByRole("textbox", { name: "Contact phone" }), "+1 555 010 2000");
    await user.click(screen.getByRole("button", { name: "Publish offer" }));

    await waitFor(() => expect(discovery.publishOffer).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: "SERVICE",
        product_code: "SERVICE.COMPUTER.REPAIR",
        description: "Computer repair",
        unit_code: "HOUR",
      }),
      "service-member-1",
    ));
  });
  it("uploads an offer image before publishing the service", async () => {
    const user = userEvent.setup();
    const view = renderView({
      ...principal,
      member_id: "service-member-1",
      roles: [{ assignment_id: "role-participant", role: "EXCHANGE_PARTICIPANT", cooperative_id: "30000000-0000-0000-0000-000000000001" }],
    });

    await user.click(screen.getByRole("tab", { name: "Offer" }));
    await user.click(screen.getByRole("button", { name: /^Service$/ }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Service type" }), "SERVICE.CUSTOM");
    await user.type(screen.getByRole("textbox", { name: "Service name" }), "Tractor repair");
    const file = new File([new Uint8Array([137, 80, 78, 71])], "tractor.png", { type: "image/png" });
    const input = view.container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();
    await user.upload(input!, file);
    expect(screen.getByText("tractor.png")).toBeInTheDocument();

    await user.type(screen.getByRole("textbox", { name: /Exact (pickup|service) address/ }), "12 Farm Road, Barn 2");
    await user.type(screen.getByRole("textbox", { name: "Contact phone" }), "+1 555 010 2000");
    await user.click(screen.getByRole("button", { name: "Publish offer" }));

    await waitFor(() => expect(inventory.uploadEvidence).toHaveBeenCalledWith(
      "30000000-0000-0000-0000-000000000001",
      file,
      "OFFER_IMAGE",
    ));
    expect(discovery.publishOffer).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: "SERVICE",
        product_code: "SERVICE.TRACTOR.REPAIR",
        image_evidence_id: "evidence-image-1",
      }),
      "service-member-1",
    );
  });
  it("switches the participant offer form to a plain service workflow", async () => {
    const user = userEvent.setup();
    renderView({
      ...principal,
      member_id: "service-member-1",
      roles: [{ assignment_id: "role-participant", role: "EXCHANGE_PARTICIPANT", cooperative_id: "30000000-0000-0000-0000-000000000001" }],
    });

    await user.click(screen.getByRole("tab", { name: "Offer" }));
    await user.click(screen.getByRole("button", { name: /^Service$/ }));

    expect(screen.getByRole("heading", { name: "Offer a service" })).toBeInTheDocument();
    expect(screen.getByText("State the available capacity, deadline, and a clear service outcome.")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Service type" })).toHaveValue("SERVICE.COMPUTER.REPAIR");
    expect(screen.getByRole("textbox", { name: "Service name" })).toHaveValue("Computer repair");
    expect(screen.getByRole("combobox", { name: "Unit" })).toHaveValue("HOUR");
    expect(screen.getByText("8.00 hr", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("Choose a photo")).toBeInTheDocument();
  });
  it("keeps interrupted commit and cancellation visible and retryable", async () => {    const now = "2026-07-21T10:00:00Z";
    const baseIntent: discovery.PurchaseIntent = {
      id: "intent-commit",
      buyer_node_code: "node-local-01",
      buyer_member_id: "member-1",
      offer_record_id: "offer-record-1",
      quote_record_id: "quote-record-1",
      quantity: "10.000",
      unit_code: "KG",
      destination_region: "EAST-DISTRICT",
      delivery_address_text: "12 Farm Road, Barn 2",
      delivery_contact_name: "John Buyer",
      delivery_contact_phone: "+1 555 010 2000",
      delivery_instructions: "Call at the gate",
      max_landed_cost: "65.00",
      landed_cost_breakdown: { landed_cost: "65.00" },
      cost_status: "CONFIRMED",
      summary_hash: "sha256:summary",
      status: "COMMITTING",
      commit_request_hash: "sha256:commit",
      commit_expected_version: 3,
      cancellation_expected_version: null,
      created_at: now,
      expires_at: "2026-07-21T11:00:00Z",
      committed_at: null,
      closed_at: null,
      version: 5,
    };
    const cancellingIntent: discovery.PurchaseIntent = {
      ...baseIntent,
      id: "intent-cancel",
      status: "CANCELLING",
      commit_request_hash: null,
      commit_expected_version: null,
      cancellation_expected_version: 4,
      version: 6,
    };
    vi.mocked(discovery.getPurchaseIntents).mockResolvedValue([
      baseIntent,
      cancellingIntent,
    ]);
    const user = userEvent.setup();
    renderView();

    await user.click(screen.getByRole("tab", { name: "My orders" }));
    expect(await screen.findByText("Nodes are confirming the exchange")).toBeInTheDocument();
    expect(screen.getByText("Releasing reservations")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Confirm exchange" }));
    await waitFor(() =>
      expect(vi.mocked(discovery.commitPurchase).mock.calls[0]?.[0]).toEqual(
        baseIntent,
      ),
    );

    const cancelButton = screen.getByTitle("Cancel");
    await waitFor(() => expect(cancelButton).toBeEnabled());
    await user.click(cancelButton);
    await waitFor(() =>
      expect(discovery.cancelPurchase).toHaveBeenCalledWith(
        cancellingIntent,
        expect.any(String),
      ),
    );
  });});
