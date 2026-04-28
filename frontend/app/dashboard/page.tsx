/**
 * app/dashboard/page.tsx — Server Component
 * Tai health, status, balance, stats tu FastAPI khi render.
 */
import StatusBadge from "@/components/StatusBadge";
import type {
  HealthResponse,
  StatusResponse,
  StatsResponse,
  BalanceResponse,
  DerivHealthResponse,
} from "@/lib/api";

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
  const [health, status, stats, balance, derivHealth] = await Promise.all([
    safeGet<HealthResponse>(`${API}/health`),
    safeGet<StatusResponse>(`${API}/status`),
    safeGet<StatsResponse>(`${API}/stats`),
    safeGet<BalanceResponse>(`${API}/balance`),
    safeGet<DerivHealthResponse>(`${API}/health/deriv`),
  ]);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-100">Tong quan</h1>

      {/* Tong quan suc khoe */}
      <section>
        <h2 className="text-sm font-semibold text-gray-400 uppercase mb-3">Suc khoe he thong</h2>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          {health ? (
            <>
              <HealthCard label="Tong the"      status={health.status} />
              <HealthCard label="Redis"        status={health.checks.redis.status} />
              <HealthCard label="Token Deriv"  status={health.checks.deriv_token.status} />
              <HealthCard label="SQLite"       status={health.checks.sqlite?.status ?? "unknown"} />
              <HealthCard label="Kho vector"   status={health.checks.vector_store.status} />
              <HealthCard label="Dong co"      status={health.checks.engine_mode?.mode ?? "?"} raw />
            </>
          ) : (
            <p className="col-span-6 text-red-400 text-sm">Khong ket noi duoc den FastAPI backend.</p>
          )}
        </div>
      </section>

      {/* Hang thong ke */}
      <section className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="So du"        value={balance ? `${balance.balance.toFixed(2)} ${balance.currency}` : "—"} />
        <StatCard label="Ty le thang"  value={stats   ? `${stats.win_rate_pct?.toFixed(1) ?? "—"}%` : "—"} />
        <StatCard label="So lenh"      value={stats   ? String(stats.total_trades ?? "—") : "—"} />
        <StatCard label="Che do"       value={status  ? formatEngineMode(status.engine_mode) : "—"} />
      </section>

      {/* Symbol dang kich hoat */}
      {status && (
        <section>
          <h2 className="text-sm font-semibold text-gray-400 uppercase mb-2">Symbol dang kich hoat</h2>
          <div className="flex flex-wrap gap-2">
            {status.active_symbols.map((sym) => (
              <span key={sym} className="badge-ok">{sym}</span>
            ))}
          </div>
        </section>
      )}

      {/* Tep model */}
      {health && (
        <section>
          <h2 className="text-sm font-semibold text-gray-400 uppercase mb-2">Tep model</h2>
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

      {/* Deriv health chi tiet */}
      <section>
        <h2 className="text-sm font-semibold text-gray-400 uppercase mb-2">Deriv health chi tiet</h2>
        {derivHealth ? (
          <div className="card space-y-3 text-sm">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <HealthCard label="Tong trang thai" status={derivHealth.status} />
              <HealthCard label="Token" status={derivHealth.token_present ? "ok" : "missing"} />
              <HealthCard label="Ket noi broker" status={derivHealth.broker_reachable ? "ok" : "error"} />
              <HealthCard label="San sang dat lenh" status={derivHealth.order_capable ? "ok" : "degraded"} />
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <MetricRow label="Buoc" value={derivHealth.stage} />
              <MetricRow label="Symbol" value={derivHealth.symbol} />
              <MetricRow label="Timeout" value={`${derivHealth.timeout_seconds}s`} />
              <MetricRow
                label="Tong latency"
                value={derivHealth.latency_ms.total != null ? `${derivHealth.latency_ms.total} ms` : "—"}
              />
            </div>

            <div className="grid grid-cols-3 gap-3">
              <MetricRow
                label="Ket noi"
                value={derivHealth.latency_ms.connect != null ? `${derivHealth.latency_ms.connect} ms` : "—"}
              />
              <MetricRow
                label="Xac thuc"
                value={derivHealth.latency_ms.authorize != null ? `${derivHealth.latency_ms.authorize} ms` : "—"}
              />
              <MetricRow
                label="Proposal"
                value={derivHealth.latency_ms.proposal != null ? `${derivHealth.latency_ms.proposal} ms` : "—"}
              />
            </div>

            <p className="text-xs text-gray-500">{derivHealth.detail || "Khong co thong tin chi tiet"}</p>
          </div>
        ) : (
          <p className="text-red-400 text-sm">Khong tai duoc thong tin /health/deriv.</p>
        )}
      </section>
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

function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-gray-500">{label}</p>
      <p className="text-sm font-semibold text-gray-200">{value}</p>
    </div>
  );
}

function formatEngineMode(mode: string): string {
  if (mode === "LIVE") return "Thuc chien";
  if (mode === "PAPER") return "Mo phong";
  if (mode === "PAUSED") return "Tam dung";
  if (mode === "STOPPED") return "Da dung";
  return mode;
}
