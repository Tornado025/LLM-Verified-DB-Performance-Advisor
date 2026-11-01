import type { components } from "./api.d";
export type { components, paths, operations } from "./api.d";
export type JobRequest = components["schemas"]["JobRequest"];
export type StateName = "PASS" | "FAIL" | "BLOCK" | "CONTINUE";
export type CandidateStatus =
  | "proposed"
  | "rejected_safety"
  | "rejected_invalid_ddl"
  | "skipped_duplicate"
  | "skipped_noplan"
  | "rejected_equiv"
  | "dropped_race"
  | "failed_guard"
  | "blocked_unproven"
  | "selected"
  | "approved"
  | "applied"
  | "reverted"
  | "blocked_by_human";
export interface Health {
  status: string;
  postgres: boolean;
  hypopg: boolean;
  pg_stat_statements: boolean;
  golden_idle: boolean;
  llm: { provider: string; model: string; ok: boolean };
  version: string;
}
export interface Job {
  id: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  created_at: string;
  params: JobRequest;
  result: Record<string, unknown> | null;
  candidates?: Candidate[];
}
export interface Candidate {
  id: string;
  job_id: string;
  shape_id: string;
  kind: "index" | "stats" | "rewrite";
  sql: string;
  status: CandidateStatus;
  reason: string | null;
  guard: Guard | null;
  equiv: Equivalence | null;
  rationale?: string;
  signature?: string | null;
  speedup?: Summary | null;
  plan_before?: Plan | null;
  plan_after?: Plan | null;
  apply_preview?: string | null;
  target_database?: string;
}
export interface Summary {
  n: number;
  mean_rel: number | null;
  lower: number | null;
  upper: number | null;
  state: StateName;
  metric?: string;
  n_runs?: number;
}
export interface Guard {
  passed: boolean;
  failed_cell?: [string, string] | null;
  profiles: Record<string, { cells: Record<string, Summary> }>;
  blocks?: Record<string, number>;
}
export interface Equivalence {
  label: "tested" | "rejected" | "unknown";
  tier_reached: number;
  rewrite_class: number | null;
  weak: boolean;
  reason?: string | null;
  cx_dbs: number;
  nonvacuous_fraction: number | null;
  counterexample?: Record<string, unknown> | null;
}
export interface Plan {
  Plan?: Plan;
  Plans?: Plan[];
  "Node Type"?: string;
  "Relation Name"?: string;
  "Index Name"?: string;
  "Plan Rows"?: number;
  [key: string]: unknown;
}
export interface Evidence {
  job_id: string;
  shape_id: string;
  baseline: { sql: string; signature: string | null; plan: Plan };
  candidates: Candidate[];
  settings: {
    delta: number;
    alpha: number;
    metric: string;
    profiles: string[];
  };
  decision: {
    action: string;
    candidate_id: string | null;
    reason: string;
    requires_confirmation?: boolean;
  };
  budget: {
    seconds: number;
    paired_runs: number;
    llm_calls: number;
    clones_created: number;
  };
  propose?: { usage?: { cost?: number | null } };
}
export interface JobEvent {
  id: number;
  job_id: string;
  ts: string;
  step: string;
  level: string;
  message: string;
  data: Record<string, unknown> | null;
}
export interface Profile {
  name: string;
  rate_mult: number;
  hot_share: number;
  hot_set_size: number;
}
export interface Shapes {
  reports: {
    id: string;
    name: string;
    question: string;
    sql_template: string;
  }[];
  txns: { id: string; weight: number; description: string }[];
}
export interface Applied {
  id: string;
  candidate_id: string;
  state: string;
  windows: Record<string, unknown>[];
  latest_window: Record<string, unknown> | null;
  kind: string;
  ddl: string | null;
  rewrite_sql: string | null;
  applied_at: string;
  baseline: { groups: Record<string, { mean_ms: number | null }> } | null;
}
export const terminal = (status?: string) =>
  ["done", "failed", "cancelled"].includes(status ?? "");
