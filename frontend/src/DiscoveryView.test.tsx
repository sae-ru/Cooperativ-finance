import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DiscoveryView from "./DiscoveryView";
import i18n from "./i18n";
import type { Principal } from "./api/admin";
import * as discovery from "./api/discovery";

vi.mock("./api/discovery", async () => {
  const actual = await vi.importActual<typeof import("./api/discovery")>("./api/discovery");
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
    vi.mocked(discovery.getPurchaseIntents).mockResolvedValue([]);
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
    expect(screen.getByText("transport")).toBeInTheDocument();
    expect(screen.getByText("65 shares")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Get for shares" }));
    await waitFor(() => expect(discovery.createPurchaseIntent).toHaveBeenCalledWith(
      candidate,
      "10.000",
    ));
    expect(screen.getByRole("heading", { name: "Exchange checkout" })).toBeInTheDocument();
  });

  it("publishes a simple signed offer for the responsible catalog operator", async () => {
    const user = userEvent.setup();
    renderView({
      ...principal,
      member_id: "seller-member-1",
      roles: [{ assignment_id: "role-1", role: "NODE_BUSINESS_OPERATOR", cooperative_id: null }],
    });

    await user.click(screen.getByRole("tab", { name: "Offer" }));
    expect(screen.getByRole("heading", { name: "Offer goods" })).toBeInTheDocument();
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
