import { useQuery } from "@tanstack/react-query";

import { fetchSystemStatus } from "../../api/system";

export const systemStatusQueryKey = ["system", "status"] as const;

export function useSystemStatus() {
  return useQuery({
    queryKey: systemStatusQueryKey,
    queryFn: ({ signal }) => fetchSystemStatus(signal),
    refetchInterval: 10_000,
    retry: 2,
    retryDelay: (attempt) => Math.min(1_000 * 2 ** attempt, 5_000),
    staleTime: 5_000
  });
}
