import type { components } from "./schema";
import { commandHeaders, request } from "./admin";

export type AntifraudOverview = components["schemas"]["OverviewResponse"];
export type AntifraudScan = components["schemas"]["ScanResponse"];
export type AntifraudSignal = components["schemas"]["SignalResponse"];
export type AntifraudRule = components["schemas"]["RuleResponse"];
export type AntifraudRuleCatalog = components["schemas"]["RuleCatalogResponse"];

type CommandResult = { event_id: string; object_id: string; replayed: boolean };

function query(values: Record<string, string | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value) params.set(key, value);
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

export const getAntifraudRules = () =>
  request<AntifraudRuleCatalog>("/api/v1/antifraud/rules");

export const getAntifraudOverview = (cooperativeId?: string) =>
  request<AntifraudOverview>(
    `/api/v1/antifraud/overview${query({ cooperative_id: cooperativeId })}`,
  );

export const getAntifraudScans = (cooperativeId?: string) =>
  request<AntifraudScan[]>(
    `/api/v1/antifraud/scans${query({ cooperative_id: cooperativeId })}`,
  );

export const getAntifraudSignals = (filters: {
  cooperativeId?: string;
  status?: string;
  severity?: string;
}) =>
  request<AntifraudSignal[]>(
    `/api/v1/antifraud/signals${query({
      cooperative_id: filters.cooperativeId,
      status: filters.status,
      severity: filters.severity,
    })}`,
  );

export const runAntifraudScan = (cooperativeId: string, lookbackHours: number) =>
  request<CommandResult>("/api/v1/antifraud/scans", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({
      cooperative_id: cooperativeId,
      lookback_hours: lookbackHours,
    }),
  });

export const beginAntifraudReview = (signal: AntifraudSignal) =>
  request<CommandResult>(`/api/v1/antifraud/signals/${signal.id}/review`, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({ expected_version: signal.version }),
  });

export const decideAntifraudSignal = (
  signal: AntifraudSignal,
  payload: {
    decision: "CLEARED" | "CONFIRMED";
    rationale: string;
    evidence_ids: string[];
  },
) =>
  request<CommandResult>(`/api/v1/antifraud/signals/${signal.id}/decision`, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({ ...payload, expected_version: signal.version }),
  });
