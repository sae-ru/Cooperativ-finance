import { afterEach, describe, expect, it, vi } from "vitest";

import { login, logout } from "./admin";
import {
  archiveParticipantAddress,
  createParticipantAddress,
  getParticipantAddresses,
  updateParticipantAddress,
  type ParticipantAddress,
  type ParticipantAddressDraft,
} from "./participant";

function response(body: object | null, status = 200): Response {
  return new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Request-ID": "request-1" },
  });
}

describe("participant address API client", () => {
  afterEach(async () => {
    vi.unstubAllGlobals();
    try {
      await logout();
    } catch {
      // No active mocked transport remains after each test.
    }
  });

  it("lists, creates, updates, and archives private address points", async () => {
    const address: ParticipantAddress = {
      id: "address-1",
      cooperative_id: "coop-1",
      label: "Farm",
      purpose: "BOTH",
      region_code: "EAST-DISTRICT",
      address_text: "12 Farm Road",
      contact_name: "Ivan",
      contact_phone: "+1 555 010 2000",
      instructions: "Use the green gate",
      is_default_pickup: true,
      is_default_delivery: true,
      status: "ACTIVE",
      created_at: "2026-07-24T10:00:00Z",
      updated_at: "2026-07-24T10:00:00Z",
      version: 3,
    };
    const draft: ParticipantAddressDraft = {
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
    };
    const command = { data: { event_id: "event-1", object_id: address.id, replayed: false } };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ data: { access_token: "access", principal: {} } }))
      .mockResolvedValueOnce(response({ data: [address] }))
      .mockResolvedValueOnce(response(command, 201))
      .mockResolvedValueOnce(response(command))
      .mockResolvedValueOnce(response(command));
    vi.stubGlobal("fetch", fetchMock);

    await login("farmer", "password");
    expect(await getParticipantAddresses()).toEqual([address]);
    await createParticipantAddress(draft);
    await updateParticipantAddress(address, draft);
    await archiveParticipantAddress(address);

    const createRequest = fetchMock.mock.calls[2]?.[1] as RequestInit;
    const updateRequest = fetchMock.mock.calls[3]?.[1] as RequestInit;
    const archiveRequest = fetchMock.mock.calls[4]?.[1] as RequestInit;
    expect(new Headers(createRequest.headers).get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(String(updateRequest.body))).toEqual({
      ...draft,
      expected_version: 3,
    });
    expect(JSON.parse(String(archiveRequest.body))).toEqual({ expected_version: 3 });
  });
});
