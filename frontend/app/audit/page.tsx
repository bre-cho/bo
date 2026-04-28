"use client";
/**
 * app/audit/page.tsx — Man hinh nhat ky kiem toan (luu trong SQLite).
 */
import { useEffect, useState, useCallback } from "react";
import { api, type AuditRecord, type DerivHealthHistoryRecord } from "@/lib/api";

export default function AuditPage() {
  const [page,    setPage]    = useState(1);
  const [total,   setTotal]   = useState(0);
  const [records, setRecords] = useState<AuditRecord[]>([]);
  const [derivRecords, setDerivRecords] = useState<DerivHealthHistoryRecord[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (p: number) => {
    setLoading(true);
    try {
      const [res, derivHist] = await Promise.all([
        api.auditLogs(p, 50),
        api.derivHealthHistory(30),
      ]);
      setRecords(res.records);
      setTotal(res.total);
      setDerivRecords(derivHist.records);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(page); }, [page, load]);

  const totalPages = Math.max(1, Math.ceil(total / 50));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-100">Nhat ky kiem toan</h1>
        <span className="text-sm text-gray-400">{total} ban ghi (SQLite)</span>
      </div>
      <p className="text-xs text-gray-500">
        Moi API thay doi du lieu (POST/PUT/DELETE) deu duoc ghi lai voi IP goi, dau API key va ma phan hoi.
      </p>

      <section className="card space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-300 uppercase">Lich su Deriv health latency</h2>
          <span className="text-xs text-gray-500">{derivRecords.length} mau gan nhat</span>
        </div>
        {derivRecords.length === 0 ? (
          <p className="text-gray-500 text-sm">Chua co du lieu /health/deriv/history.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b text-left text-gray-400 text-xs uppercase" style={{ borderColor: "var(--border)" }}>
                  <th className="py-2 pr-4">Thoi gian</th>
                  <th className="py-2 pr-4">Trang thai</th>
                  <th className="py-2 pr-4">Giai doan</th>
                  <th className="py-2 pr-4">Tong ms</th>
                  <th className="py-2 pr-4">Ket noi</th>
                  <th className="py-2 pr-4">Xac thuc</th>
                  <th className="py-2 pr-4">Bao gia</th>
                </tr>
              </thead>
              <tbody>
                {derivRecords.map((r, idx) => (
                  <tr
                    key={`${r.timestamp}-${idx}`}
                    className="border-b hover:bg-gray-800 transition-colors"
                    style={{ borderColor: "var(--border)" }}
                  >
                    <td className="py-2 pr-4 text-gray-400 font-mono text-xs">
                      {r.timestamp ? new Date(r.timestamp).toLocaleString("vi-VN") : "—"}
                    </td>
                    <td className="py-2 pr-4">
                      <span className={r.status === "ok" ? "badge-ok" : r.status === "degraded" ? "badge-degraded" : "badge-error"}>
                        {r.status}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-blue-300 text-xs font-mono">{r.stage}</td>
                    <td className="py-2 pr-4 text-gray-200">{r.latency_ms.total ?? "—"}</td>
                    <td className="py-2 pr-4 text-gray-400">{r.latency_ms.connect ?? "—"}</td>
                    <td className="py-2 pr-4 text-gray-400">{r.latency_ms.authorize ?? "—"}</td>
                    <td className="py-2 pr-4 text-gray-400">{r.latency_ms.proposal ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {loading ? (
        <p className="text-gray-500 text-sm">Dang tai…</p>
      ) : records.length === 0 ? (
        <p className="text-gray-500 text-sm">Chua co ban ghi kiem toan.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b text-left text-gray-400 text-xs uppercase" style={{ borderColor: "var(--border)" }}>
                <th className="py-2 pr-4">Thoi gian</th>
                <th className="py-2 pr-4">Phuong thuc</th>
                <th className="py-2 pr-4">Diem cuoi</th>
                <th className="py-2 pr-4">Trang thai</th>
                <th className="py-2 pr-4">IP</th>
                <th className="py-2 pr-4">Khoa</th>
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
        <button className="btn-ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>← Truoc</button>
        <span className="text-sm text-gray-400">Trang {page} / {totalPages}</span>
        <button className="btn-ghost" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>Sau →</button>
      </div>
    </div>
  );
}
