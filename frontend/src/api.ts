import type {
  Health,
  Job,
  JobRequest,
  Candidate,
  JobEvent,
  Profile,
  Shapes,
  Applied,
  Evidence,
} from "./types";
export class ApiError extends Error {
  constructor(
    public code: string,
    message = code,
    public detail: Record<string, unknown> = {},
    public status = 0,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new ApiError("network", "Cannot reach the advisor service");
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const error = body?.error;
    throw new ApiError(
      error?.code ?? "http_error",
      error?.message ?? `Request failed (${response.status})`,
      error?.detail ?? {},
      response.status,
    );
  }
  return body as T;
}
const post = <T>(path: string, body: unknown = {}) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });
const id = (value: string) => encodeURIComponent(value);
export const getHealth = (signal?: AbortSignal) =>
  api<Health>("/health", { signal });
export const getShapes = (signal?: AbortSignal) =>
  api<Shapes>("/shapes", { signal });
export const getProfiles = (signal?: AbortSignal) =>
  api<Profile[]>("/profiles", { signal });
export const getObserve = (signal?: AbortSignal) =>
  api<{ available: boolean; statements: Record<string, unknown>[] }>(
    "/observe",
    { signal },
  );
export const createJob = (body: JobRequest) =>
  post<{ job_id: string }>("/jobs", body);
export const listJobs = (signal?: AbortSignal) =>
  api<Job[]>("/jobs", { signal });
export const getJob = (jobId: string, signal?: AbortSignal) =>
  api<Job>(`/jobs/${id(jobId)}`, { signal });
export const cancelJob = (jobId: string) =>
  post<{ status: string }>(`/jobs/${id(jobId)}/cancel`);
export const getEvidence = (jobId: string, signal?: AbortSignal) =>
  api<{ cards: Evidence[] }>(`/jobs/${id(jobId)}/evidence`, {
    signal,
  });
export const getCandidate = (candidateId: string, signal?: AbortSignal) =>
  api<Candidate>(`/candidates/${id(candidateId)}`, { signal });
export const approve = (candidateId: string, confirm = false) =>
  post<Applied>(`/candidates/${id(candidateId)}/approve`, { confirm });
export const blockCandidate = (candidateId: string, reason?: string) =>
  post<Candidate>(`/candidates/${id(candidateId)}/block`, { reason });
export const listApplied = (signal?: AbortSignal) =>
  api<{ applied: Applied[] }>("/applied", { signal });
export const revert = (appliedId: string, reason?: string) =>
  post<Applied>(`/applied/${id(appliedId)}/revert`, { reason });
export const traffic = (body: {
  action: "start" | "stop";
  profile?: string;
  seconds?: number;
}) =>
  post<{ running: boolean; profile: string | null; started_at: string | null }>(
    "/live/traffic",
    body,
  );
export const getTraffic = (signal?: AbortSignal) =>
  api<{
    running: boolean;
    profile: string | null;
    recent: Record<string, unknown>[];
  }>("/live/traffic", { signal });
export const getEvents = (jobId: string, after = 0, signal?: AbortSignal) =>
  api<{ events: JobEvent[]; last_id: number }>(
    `/jobs/${id(jobId)}/events.json?after=${after}`,
    { signal },
  );
export const getRq2 = (signal?: AbortSignal) =>
  api<Record<string, unknown>>("/experiments/rq2", { signal });
export const getRq4 = (signal?: AbortSignal) =>
  api<Record<string, unknown>>("/experiments/rq4", { signal });
export const getRq5 = (signal?: AbortSignal) =>
  api<Record<string, unknown>>("/experiments/rq5", { signal });
export const getSettings = (signal?: AbortSignal) =>
  api<Record<string, unknown>>("/settings", { signal });
