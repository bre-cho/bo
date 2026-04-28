"use client";
/**
 * app/audit/page.tsx — Audit log viewer (SQLite-backed).
 */
import { useEffect, useState, useCallback } from "react";
import { api, type AuditRecord } from "@/lib/api";

export default function AuditPage() {
  const [page,    setPage]    = useState(1);
  const [total,   setTotal]   = useState(0);
  const [records, setRecords] = useState<AuditRecord[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (p: number) => {
    setLoading(true);
    try {
      const res = await api.auditLogs(p, 50);
      setRecords(res.records);
      setTotal(res.total);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(page); }, [page, load]);

  const totalPages = Math.max(1, Math.ceil(total / 50));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-100">Audit Log</h1>
        <span className="text-sm text-gray-400">{total} entries (SQLite)</span>
      </div>
      <p className="text-xs text-gray-500">
        Every mutating API call (POST/PUT/DELETE) is recorded here with caller IP, key hint, and response code.
      </p>

      {loading ? (
        <p className="text-gray-500 text-sm">Loading…</p>
      ) : records.length === 0 ? (
        <p className="text-gray-500 text-sm">No audit entries yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b text-left text-gray-400 text-xs uppercase" style={{ borderColor: "var(--border)" }}>
                <th className="py-2 pr-4">Time</th>
                <th className="py-2 pr-4">Method</th>
                <th className="py-2 pr-4">Endpoint</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4">IP</th>
                <th className="py-2 pr-4">Key</th>
                <th className="py-2 pr-4">ms</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r) => (
                <tr
                  key={r.id}
                  className="border-b hover:bg-gray-800 transition-colors"
                  style={{ borderColor: "var(--border)" }}
                >
                  <td className="py-2 pr-4 text-gray-400 font-mono text-xs">
                    {r.created_at ? new Date(r.created_at).toLocaleString("vi-VN") : "—"}
                  </td>
                  <td className="py-2 pr-4">
                    <span className={r.method === "DELETE" ? "text-red-400" : "text-yellow-300"}>
                      {r.method}
                    </span>
                  </td>
                  <td className="py-2 pr-4 font-mono text-blue-300 text-xs">{r.endpoint}</td>
                  <td className="py-2 pr-4">
                    <span className={
                      (r.status_code ?? 0) < 300 ? "badge-ok" :
                      (r.status_code ?? 0) < 500 ? "badge-degraded" :
                      "badge-error"
                    }>
                      {r.status_code ?? "?"}
                    </span>
                  </td>
                  <td className="py-2 pr-4 text-gray-400 text-xs">{r.ip_address ?? "—"}</td>
                  <td className="py-2 pr-4 text-gray-500 font-mono text-xs">{r.api_key_hint ?? "—"}</td>
                  <td className="py-2 pr-4 text-gray-400">{r.duration_ms?.toFixed(0) ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex items-center gap-3">
        <button className="btn-ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>← Prev</button>
        <span className="text-sm text-gray-400">Page {page} / {totalPages}</span>
        <button className="btn-ghost" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>Next →</button>
      </div>
    </div>
  );
}
