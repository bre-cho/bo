"use client";
/**
 * app/logs/page.tsx — Trade log viewer (paginated).
 */
import { useEffect, useState, useCallback } from "react";
import { api, type LogRecord } from "@/lib/api";

export default function LogsPage() {
  const [page,    setPage]    = useState(1);
  const [total,   setTotal]   = useState(0);
  const [records, setRecords] = useState<LogRecord[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (p: number) => {
    setLoading(true);
    try {
      const res = await api.logs(p, 20);
      setRecords(res.records);
      setTotal(res.total);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(page); }, [page, load]);

  const totalPages = Math.max(1, Math.ceil(total / 20));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-100">Trade Logs</h1>
        <span className="text-sm text-gray-400">{total} total records</span>
      </div>

      {loading ? (
        <p className="text-gray-500 text-sm">Loading…</p>
      ) : records.length === 0 ? (
        <p className="text-gray-500 text-sm">No trades yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b text-left text-gray-400 text-xs uppercase" style={{ borderColor: "var(--border)" }}>
                <th className="py-2 pr-4">Time</th>
                <th className="py-2 pr-4">Symbol</th>
                <th className="py-2 pr-4">Direction</th>
                <th className="py-2 pr-4">Stake</th>
                <th className="py-2 pr-4">Result</th>
                <th className="py-2 pr-4">Score</th>
                <th className="py-2 pr-4">Strategy</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r, i) => (
                <tr
                  key={i}
                  className="border-b hover:bg-gray-800 transition-colors"
                  style={{ borderColor: "var(--border)" }}
                >
                  <td className="py-2 pr-4 text-gray-400 font-mono text-xs">
                    {r.timestamp ? new Date(r.timestamp).toLocaleString("vi-VN") : "—"}
                  </td>
                  <td className="py-2 pr-4 font-semibold text-blue-300">{r.symbol ?? "—"}</td>
                  <td className="py-2 pr-4">
                    <span className={r.direction === "CALL" ? "text-green-400" : "text-red-400"}>
                      {r.direction ?? "—"}
                    </span>
                  </td>
                  <td className="py-2 pr-4 text-gray-200">${r.stake_usd?.toFixed(2) ?? "—"}</td>
                  <td className="py-2 pr-4">
                    <span className={
                      r.result === "WIN"  ? "badge-ok" :
                      r.result === "LOSS" ? "badge-error" :
                      "badge-missing"
                    }>
                      {r.result ?? "OPEN"}
                    </span>
                  </td>
                  <td className="py-2 pr-4 text-gray-300">{r.score?.toFixed(1) ?? "—"}</td>
                  <td className="py-2 pr-4 text-gray-400 text-xs">{r.strategy ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      <div className="flex items-center gap-3">
        <button
          className="btn-ghost"
          disabled={page <= 1}
          onClick={() => setPage((p) => p - 1)}
        >
          ← Prev
        </button>
        <span className="text-sm text-gray-400">Page {page} / {totalPages}</span>
        <button
          className="btn-ghost"
          disabled={page >= totalPages}
          onClick={() => setPage((p) => p + 1)}
        >
          Next →
        </button>
      </div>
    </div>
  );
}
