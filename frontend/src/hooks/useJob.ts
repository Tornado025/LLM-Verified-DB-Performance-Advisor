import { getJob } from "../api";
import { useApi } from "./useApi";
import { usePolling } from "./usePolling";
import { terminal } from "../types";
export function useJob(jobId: string) {
  const result = useApi((signal) => getJob(jobId, signal), [jobId]);
  usePolling(
    async () => {
      result.reload();
    },
    2000,
    !!jobId && !terminal(result.data?.status),
  );
  return result;
}
