import type { OfferDraft } from "../api/discovery";

export const BROWSER_DRAFT_FORMAT = "cooperative-browser-draft-v1" as const;
export const BROWSER_DRAFT_RETENTION_DAYS = 7;

export type BrowserOfferDraft = {
  format: typeof BROWSER_DRAFT_FORMAT;
  draft_id: string;
  kind: "MARKET_OFFER";
  owner_user_id: string;
  cooperative_id: string;
  payload: OfferDraft;
  attachment: Blob | null;
  attachment_name: string | null;
  attachment_type: string | null;
  created_at: string;
  updated_at: string;
  expires_at: string;
  authoritative: false;
  review_required: true;
};

export type SaveBrowserOfferDraftInput = {
  draft_id?: string;
  owner_user_id: string;
  cooperative_id: string;
  payload: OfferDraft;
  attachment: File | null;
};

const DATABASE_NAME = "cooperative-browser-drafts";
const DATABASE_VERSION = 2;
const STORE_NAME = "drafts";
const AUTHORITATIVE_FIELDS = new Set([
  "event_id",
  "event_hash",
  "local_sequence",
  "node_sequence",
  "object_id",
  "signature",
  "signed_at",
]);

function assertNonAuthoritative(value: unknown, path = "payload"): void {
  if (Array.isArray(value)) {
    value.forEach((item, index) => assertNonAuthoritative(item, `${path}[${index}]`));
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [key, nested] of Object.entries(value)) {
    if (AUTHORITATIVE_FIELDS.has(key)) {
      throw new Error(`BROWSER_DRAFT_AUTHORITATIVE_FIELD:${path}.${key}`);
    }
    assertNonAuthoritative(nested, `${path}.${key}`);
  }
}

function isOfferDraftPayload(value: unknown): value is OfferDraft {
  if (!value || typeof value !== "object") return false;
  const payload = value as Partial<OfferDraft>;
  const requiredStrings: Array<keyof OfferDraft> = [
    "product_code",
    "description",
    "quantity_available",
    "unit_code",
    "minimum_batch",
    "origin_region",
    "pickup_address_text",
    "pickup_contact_name",
    "pickup_contact_phone",
    "pickup_instructions",
    "unit_price",
    "available_until",
  ];
  return ["PRODUCT", "SERVICE"].includes(payload.kind ?? "")
    && requiredStrings.every((key) => typeof payload[key] === "string")
    && payload.image_evidence_id === null;
}

function randomDraftId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `draft-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function buildBrowserOfferDraft(
  input: SaveBrowserOfferDraftInput,
  existing: BrowserOfferDraft | null = null,
  now = new Date(),
): BrowserOfferDraft {
  if (!input.owner_user_id || !input.cooperative_id) {
    throw new Error("BROWSER_DRAFT_OWNER_REQUIRED");
  }
  if (existing && (
    existing.owner_user_id !== input.owner_user_id
    || existing.cooperative_id !== input.cooperative_id
  )) {
    throw new Error("BROWSER_DRAFT_SCOPE_MISMATCH");
  }
  if (!isOfferDraftPayload(input.payload)) {
    throw new Error("BROWSER_DRAFT_PAYLOAD_INVALID");
  }
  assertNonAuthoritative(input.payload);
  const timestamp = now.toISOString();
  const expiresAt = new Date(now.getTime() + BROWSER_DRAFT_RETENTION_DAYS * 24 * 60 * 60_000).toISOString();
  return {
    format: BROWSER_DRAFT_FORMAT,
    draft_id: existing?.draft_id ?? input.draft_id ?? randomDraftId(),
    kind: "MARKET_OFFER",
    owner_user_id: input.owner_user_id,
    cooperative_id: input.cooperative_id,
    payload: structuredClone(input.payload),
    attachment: input.attachment,
    attachment_name: input.attachment?.name ?? null,
    attachment_type: input.attachment?.type ?? null,
    created_at: existing?.created_at ?? timestamp,
    updated_at: timestamp,
    expires_at: expiresAt,
    authoritative: false,
    review_required: true,
  };
}

export function isBrowserDraftStorageAvailable(): boolean {
  return typeof indexedDB !== "undefined";
}

export function isValidStoredBrowserOfferDraft(value: unknown, ownerUserId: string, cooperativeId: string, now = Date.now()): value is BrowserOfferDraft {
  if (!value || typeof value !== "object") return false;
  const record = value as Partial<BrowserOfferDraft>;
  try {
    assertNonAuthoritative(record.payload);
  } catch {
    return false;
  }
  return record.format === BROWSER_DRAFT_FORMAT
    && record.kind === "MARKET_OFFER"
    && record.owner_user_id === ownerUserId
    && record.cooperative_id === cooperativeId
    && isOfferDraftPayload(record.payload)
    && record.authoritative === false
    && record.review_required === true
    && typeof record.expires_at === "string"
    && Date.parse(record.expires_at) > now;
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (!isBrowserDraftStorageAvailable()) {
      reject(new Error("BROWSER_DRAFT_STORAGE_UNAVAILABLE"));
      return;
    }
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      const store = database.objectStoreNames.contains(STORE_NAME)
        ? request.transaction!.objectStore(STORE_NAME)
        : database.createObjectStore(STORE_NAME, { keyPath: "draft_id" });
      if (!store.indexNames.contains("owner_user_id")) {
        store.createIndex("owner_user_id", "owner_user_id", { unique: false });
      }
      if (!store.indexNames.contains("owner_scope")) {
        store.createIndex("owner_scope", ["owner_user_id", "cooperative_id"], { unique: false });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("BROWSER_DRAFT_STORAGE_OPEN_FAILED"));
  });
}

export async function listBrowserOfferDrafts(ownerUserId: string, cooperativeId: string): Promise<BrowserOfferDraft[]> {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    const store = transaction.objectStore(STORE_NAME);
    const request = store.index("owner_scope").getAll([ownerUserId, cooperativeId]);
    let active: BrowserOfferDraft[] = [];
    request.onsuccess = () => {
      const now = Date.now();
      const records = request.result as unknown[];
      active = records.filter((record): record is BrowserOfferDraft => {
        const valid = isValidStoredBrowserOfferDraft(record, ownerUserId, cooperativeId, now);
        if (!valid && record && typeof record === "object" && "draft_id" in record && typeof record.draft_id === "string") {
          store.delete(record.draft_id);
        }
        return valid;
      }).sort((left, right) => right.updated_at.localeCompare(left.updated_at));
    };
    transaction.oncomplete = () => {
      database.close();
      resolve(active);
    };
    transaction.onerror = () => {
      database.close();
      reject(transaction.error ?? new Error("BROWSER_DRAFT_STORAGE_READ_FAILED"));
    };
    transaction.onabort = transaction.onerror;
  });
}

export async function saveBrowserOfferDraft(input: SaveBrowserOfferDraftInput): Promise<BrowserOfferDraft> {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    const store = transaction.objectStore(STORE_NAME);
    const request = input.draft_id ? store.get(input.draft_id) : null;
    let saved: BrowserOfferDraft | null = null;
    let failure: Error | null = null;
    const persist = (existing: BrowserOfferDraft | null) => {
      try {
        saved = buildBrowserOfferDraft(input, existing);
        store.put(saved);
      } catch (error) {
        failure = error instanceof Error ? error : new Error("BROWSER_DRAFT_STORAGE_WRITE_FAILED");
        transaction.abort();
      }
    };
    if (request) {
      request.onsuccess = () => persist((request.result as BrowserOfferDraft | undefined) ?? null);
    } else {
      persist(null);
    }
    transaction.oncomplete = () => {
      database.close();
      if (!saved) {
        reject(new Error("BROWSER_DRAFT_STORAGE_WRITE_FAILED"));
        return;
      }
      resolve(saved);
    };
    transaction.onerror = () => {
      database.close();
      reject(failure ?? transaction.error ?? new Error("BROWSER_DRAFT_STORAGE_WRITE_FAILED"));
    };
    transaction.onabort = transaction.onerror;
  });
}

export async function deleteBrowserOfferDraft(draftId: string, ownerUserId: string, cooperativeId: string): Promise<void> {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    const store = transaction.objectStore(STORE_NAME);
    const request = store.get(draftId);
    request.onsuccess = () => {
      const record = request.result as BrowserOfferDraft | undefined;
      if (record?.owner_user_id === ownerUserId && record.cooperative_id === cooperativeId) store.delete(draftId);
    };
    transaction.oncomplete = () => {
      database.close();
      resolve();
    };
    transaction.onerror = () => {
      database.close();
      reject(transaction.error ?? new Error("BROWSER_DRAFT_STORAGE_DELETE_FAILED"));
    };
    transaction.onabort = transaction.onerror;
  });
}

export function browserDraftAttachmentFile(draft: BrowserOfferDraft): File | null {
  if (!draft.attachment || !draft.attachment_name) return null;
  return new File([draft.attachment], draft.attachment_name, {
    type: draft.attachment_type ?? draft.attachment.type,
  });
}
