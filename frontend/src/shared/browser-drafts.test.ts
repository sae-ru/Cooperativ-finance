import { describe, expect, it } from "vitest";

import type { OfferDraft } from "../api/discovery";
import {
  BROWSER_DRAFT_FORMAT,
  BROWSER_DRAFT_RETENTION_DAYS,
  browserDraftAttachmentFile,
  buildBrowserOfferDraft,
  isValidStoredBrowserOfferDraft,
} from "./browser-drafts";

const offer: OfferDraft = {
  kind: "PRODUCT",
  product_code: "MILK.UHT.3_2",
  description: "Milk",
  quantity_available: "100.000",
  unit_code: "L",
  minimum_batch: "10.000",
  origin_region: "EAST-DISTRICT",
  pickup_address_text: "12 Farm Road, Barn 2",
  pickup_contact_name: "Farmer",
  pickup_contact_phone: "+1 555 010 2000",
  pickup_instructions: "Call at the gate",
  unit_price: "3.00",
  available_until: "2026-08-05",
  image_evidence_id: null,
};

describe("browser offer drafts", () => {
  it("builds an explicitly local record without server event identity", () => {
    const now = new Date("2026-07-29T10:00:00Z");
    const attachment = new File(["milk"], "milk.jpg", { type: "image/jpeg" });
    const draft = buildBrowserOfferDraft({
      owner_user_id: "user-1",
      cooperative_id: "coop-1",
      payload: offer,
      attachment,
    }, null, now);

    expect(draft).toMatchObject({
      format: BROWSER_DRAFT_FORMAT,
      kind: "MARKET_OFFER",
      owner_user_id: "user-1",
      authoritative: false,
      review_required: true,
      created_at: now.toISOString(),
      updated_at: now.toISOString(),
    });
    expect(Date.parse(draft.expires_at) - now.getTime()).toBe(BROWSER_DRAFT_RETENTION_DAYS * 24 * 60 * 60_000);
    expect(JSON.stringify(draft)).not.toContain("event_id");
    expect(browserDraftAttachmentFile(draft)).toMatchObject({ name: "milk.jpg", type: "image/jpeg" });
  });

  it("rejects authoritative identifiers anywhere in the local payload", () => {
    const contaminated = {
      ...offer,
      handling_requirements: { nested: { event_id: "server-event-1" } },
    } as unknown as OfferDraft;

    expect(() => buildBrowserOfferDraft({
      owner_user_id: "user-1",
      cooperative_id: "coop-1",
      payload: contaminated,
      attachment: null,
    })).toThrow("BROWSER_DRAFT_AUTHORITATIVE_FIELD");
    expect(isValidStoredBrowserOfferDraft({
      ...buildBrowserOfferDraft({
        owner_user_id: "user-1",
        cooperative_id: "coop-1",
        payload: offer,
        attachment: null,
      }),
      payload: contaminated,
    }, "user-1", "coop-1")).toBe(false);
    expect(isValidStoredBrowserOfferDraft({
      ...buildBrowserOfferDraft({
        owner_user_id: "user-1",
        cooperative_id: "coop-1",
        payload: offer,
        attachment: null,
      }),
      payload: null,
    }, "user-1", "coop-1")).toBe(false);
  });

  it("preserves creation time only when the same owner updates a draft", () => {
    const first = buildBrowserOfferDraft({
      owner_user_id: "user-1",
      cooperative_id: "coop-1",
      payload: offer,
      attachment: null,
    }, null, new Date("2026-07-29T10:00:00Z"));
    const updated = buildBrowserOfferDraft({
      draft_id: first.draft_id,
      owner_user_id: "user-1",
      cooperative_id: "coop-1",
      payload: { ...offer, quantity_available: "120.000" },
      attachment: null,
    }, first, new Date("2026-07-29T11:00:00Z"));

    expect(updated.draft_id).toBe(first.draft_id);
    expect(updated.created_at).toBe(first.created_at);
    expect(updated.updated_at).not.toBe(first.updated_at);
    expect(() => buildBrowserOfferDraft({
      owner_user_id: "user-2",
      cooperative_id: "coop-1",
      payload: offer,
      attachment: null,
    }, first)).toThrow("BROWSER_DRAFT_SCOPE_MISMATCH");
    expect(() => buildBrowserOfferDraft({
      owner_user_id: "user-1",
      cooperative_id: "coop-2",
      payload: offer,
      attachment: null,
    }, first)).toThrow("BROWSER_DRAFT_SCOPE_MISMATCH");
  });
});
