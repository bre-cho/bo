/**
 * app/dashboard/page.tsx — Server Component
 * Fetches health, status, balance, stats from FastAPI at render time.
 */
import StatusBadge from "@/components/StatusBadge";
import type { HealthResponse, StatusResponse, StatsResponse, BalanceResponse } from "@/lib/api";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function safeGet<T>(url: string): Promise<T | null> {
  try {
    const res = await fetch(url, { next: { revalidate: 10 } });
    if (!res.ok) return null;
    return res.json() as Promise<T>;
  } catch {
    return null;
  }
}

export default async function DashboardPage() {
  const [health, status, stats, balance] = await Promise.all([
    safeGet<HealthResponse>(`${API}/health`),
    safeGet<StatusResponse>(`${API}/status`),
    safeGet<StatsResponse>(`${API}/stats`),
    safeGet<BalanceResponse>(`${API}/balance`),
  ]);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-100">Dashboard</h1>

      {/* Health overview */}
      <section>
        <h2 className="text-sm font-semibold text-gray-400 uppercase mb-3">System Health</h2>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          {health ? (
            <>
              <HealthCard label="Overall"      status={health.status} />
              <HealthCard label="Redis"        status={health.checks.redis.status} />
              <HealthCard label="Deriv Token"  status={health.checks.deriv_token.status} />
              <HealthCard label="SQLite"       status={health.checks.sqlite?.status ?? "unknown"} />
              <HealthCard label="Vector Store" status={health.checks.vector_store.status} />
              <HealthCard label="Engine"       status={health.checks.engine_mode?.mode ?? "?"} raw />
            </>
          ) : (
            <p className="col-span-6 text-red-400 text-sm">Could not reach FastAPI backend.</p>
          )}
        </div>
      </section>

      {/* Stats row */}
      <section className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Balance"   value={balance ? `${balance.balance.toFixed(2)} ${balance.currency}` : "—"} />
        <StatCard label="Win Rate"  value={stats   ? `${stats.win_rate_pct?.toFixed(1) ?? "—"}%` : "—"} />
        <StatCard label="Trades"    value={stats   ? String(stats.total_trades ?? "—") : "—"} />
        <StatCard label="Mode"      value={status  ? status.engine_mode : "—"} />
      </section>

      {/* Active symbols */}
      {status && (
        <section>
          <h2 className="text-sm font-semibold text-gray-400 uppercase mb-2">Active Symbols</h2>
          <div className="flex flex-wrap gap-2">
            {status.active_symbols.map((sym) => (
              <span key={sym} className="badge-ok">{sym}</span>
            ))}
          </div>
        </section>
      )}

      {/* Model files */}
      {health && (
        <section>
          <h2 className="text-sm font-semibold text-gray-400 uppercase mb-2">Model Files</h2>
          <div className="card grid grid-cols-3 gap-3 text-sm">
            {Object.entries(health.checks.model_files).map(([name, s]) => (
              <div key={name} className="flex items-center gap-2">
                <StatusBadge status={s} />
                <span className="text-gray-300">{name}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function HealthCard({ label, status, raw }: { label: string; status: string; raw?: boolean }) {
  return (
    <div className="card flex flex-col gap-1">
      <span className="text-xs text-gray-500">{label}</span>
      {raw ? (
        <span className="font-semibold text-blue-300 text-sm">{status}</span>
      ) : (
        <StatusBadge status={status} />
      )}
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="card">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className="text-xl font-bold text-gray-100">{value}</p>
    </div>
  );
}
