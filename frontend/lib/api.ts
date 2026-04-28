/**
 * lib/api.ts
 * API client co kieu du lieu cho backend FastAPI.
 *
 * Moi duong dan la tuong doi de Next.js rewrite /api/*
 * ve FastAPI backend.
 */

const BASE = "/api";

function apiKey(): string {
  return process.env.NEXT_PUBLIC_API_KEY ?? "";
}

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  const key = apiKey();
  if (key) headers["X-API-Key"] = key;

  const res = await fetch(`${BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// ── Types ────────────────────────────────────────────────────────

export interface HealthChecks {
  redis:        { status: string };
  deriv_token:  { status: string; detail?: string };
  model_files:  Record<string, string>;
  vector_store: { status: string };
  sqlite:       { status: string; path?: string };
  engine_mode:  { status: string; mode?: string };
}

export interface HealthResponse {
  status: "ok" | "degraded";
  checks: HealthChecks;
}

export interface StatusResponse {
  timestamp:        string;
  engine_mode:      string;
  active_symbols:   string[];
  control:          Record<string, unknown>;
  capital_strategy: Record<string, unknown>;
}

export interface BalanceResponse {
  balance:  number;
  currency: string;
}

export interface StatsResponse {
  total_trades:     number;
  wins:             number;
  losses:           number;
  win_rate_pct:     number;
  total_profit_usd: number;
  [key: string]: unknown;
}

export interface LogRecord {
  symbol:      string;
  direction:   string;
  stake_usd:   number;
  result:      string;
  score:       number;
  strategy:    string;
  timestamp:   string;
  [key: string]: unknown;
}

export interface LogsResponse {
  total:   number;
  page:    number;
  size:    number;
  records: LogRecord[];
}

export interface AuditRecord {
  id:           number;
  created_at:   string;
  endpoint:     string;
  method:       string;
  status_code:  number;
  api_key_hint: string | null;
  ip_address:   string | null;
  duration_ms:  number | null;
}

export interface AuditLogsResponse {
  total:   number;
  page:    number;
  size:    number;
  records: AuditRecord[];
}

export interface EvolutionRunRecord {
  id:            number;
  created_at:    string;
  genome_id:     string;
  generation:    number;
  fitness:       number;
  win_rate_pct:  number | null;
  profit_factor: number | null;
  n_trades:      number | null;
  promoted:      boolean;
}

export interface EvolutionHistoryResponse {
  total:   number;
  page:    number;
  size:    number;
  records: EvolutionRunRecord[];
}

export interface DerivCheckResponse {
  configured:        boolean;
  token_present:     boolean;
  broker_reachable:  boolean;
  order_capable:     boolean;
  stage:             string;
  app_id:            number;
  ws_url:            string;
  symbol:            string;
  token_hint:        string | null;
  detail:            string;
}

export interface DerivHealthResponse {
  status:            "ok" | "degraded" | "missing";
  configured:        boolean;
  token_present:     boolean;
  broker_reachable:  boolean;
  order_capable:     boolean;
  stage:             string;
  app_id:            number;
  ws_url:            string;
  symbol:            string;
  token_hint:        string | null;
  timeout_seconds:   number;
  latency_ms: {
    connect:   number | null;
    authorize: number | null;
    proposal:  number | null;
    total:     number | null;
  };
  detail:            string;
}

export interface DerivHealthHistoryRecord {
  timestamp:        string;
  status:           "ok" | "degraded" | "missing";
  stage:            string;
  symbol:           string;
  token_present:    boolean;
  broker_reachable: boolean;
  order_capable:    boolean;
  timeout_seconds:  number;
  latency_ms: {
    connect:   number | null;
    authorize: number | null;
    proposal:  number | null;
    total:     number | null;
  };
  detail: string;
}

export interface DerivHealthHistoryResponse {
  status: "ok";
  n: number;
  records: DerivHealthHistoryRecord[];
}

// ── API calls ────────────────────────────────────────────────────

export const api = {
  health: ()                         => request<HealthResponse>("/health"),
  status: ()                         => request<StatusResponse>("/status"),
  balance: ()                        => request<BalanceResponse>("/balance"),
  stats: ()                          => request<StatsResponse>("/stats"),
  derivCheck: ()                     => request<DerivCheckResponse>("/deriv/check"),
  derivHealth: (timeout_seconds = 6) => request<DerivHealthResponse>(`/health/deriv?timeout_seconds=${timeout_seconds}`),
  derivHealthHistory: (n = 30)       => request<DerivHealthHistoryResponse>(`/health/deriv/history?n=${n}`),

  logs: (page = 1, size = 20)        => request<LogsResponse>(`/logs?page=${page}&size=${size}`),
  auditLogs: (page = 1, size = 50)   => request<AuditLogsResponse>(`/audit/logs?page=${page}&size=${size}`),
  evolutionHistory: (page = 1)       => request<EvolutionHistoryResponse>(`/db/evolution?page=${page}`),

  // Dieu khien (can API key)
  enginePause:  ()                   => request<{ status: string }>("/engine/pause",  { method: "POST" }),
  engineResume: ()                   => request<{ status: string }>("/engine/resume", { method: "POST" }),
  engineStop:   ()                   => request<{ status: string }>("/engine/stop",   { method: "POST" }),
  setDailyTp:   (amount_usd: number) => request("/control/tp",  { method: "POST", body: JSON.stringify({ amount_usd }) }),
  setDailySl:   (amount_usd: number) => request("/control/sl",  { method: "POST", body: JSON.stringify({ amount_usd }) }),
  restartEngine: ()                  => request("/control/restart", { method: "POST", body: JSON.stringify({}) }),

  setStrategy: (name: string, base_stake = 1.0) =>
    request("/strategy", { method: "POST", body: JSON.stringify({ name, base_stake }) }),

  evolutionRun: (generations = 5, pop_size = 10) =>
    request("/evolution/run", {
      method: "POST",
      body: JSON.stringify({ generations, pop_size, seed: 42 }),
    }),
  evolutionPromote: () => request("/evolution/promote", { method: "POST", body: JSON.stringify({}) }),
  evolutionChampion: () => request("/evolution/champion"),
  evolutionStatus: ()   => request("/evolution/status"),
};
