"use client";
/**
 * app/controls/page.tsx — Client Component
 * Dieu khien dong co, TP/SL, doi chien luoc von.
 * Can header X-API-Key (dat trong .env.local).
 */
import { useState } from "react";
import ActionButton from "@/components/ActionButton";
import { api } from "@/lib/api";

const STRATEGIES = [
  "fixed_fractional",
  "martingale",
  "anti_martingale",
  "victor2",
  "victor3",
  "victor4",
];

export default function ControlsPage() {
  const [tp, setTp]   = useState("50");
  const [sl, setSl]   = useState("20");
  const [strat, setStrat] = useState("fixed_fractional");
  const [stake, setStake] = useState("1.0");

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold text-gray-100">Dieu khien</h1>

      {/* Dong co */}
      <section className="card space-y-4">
        <h2 className="font-semibold text-gray-200">Che do dong co</h2>
        <div className="flex flex-wrap gap-3">
          <ActionButton label="Tam dung (→ Mo phong)" onConfirm={api.enginePause} />
          <ActionButton label="Tiep tuc (→ Thuc chien)"  onConfirm={api.engineResume} />
          <ActionButton label="Dung"               onConfirm={api.engineStop} variant="danger" />
        </div>
        <p className="text-xs text-gray-500">
          Tam dung se chuyen sang PAPER (khong vao lenh that). Dung se tat dong co hoan toan.
        </p>
      </section>

      {/* TP / SL trong ngay */}
      <section className="card space-y-4">
        <h2 className="font-semibold text-gray-200">Chot lai / Chot lo trong ngay (USD)</h2>
        <div className="flex flex-wrap gap-6 items-end">
          <div className="space-y-1">
            <label className="text-xs text-gray-400">TP trong ngay (USD)</label>
            <input
              type="number" min="0" step="1"
              value={tp}
              onChange={(e) => setTp(e.target.value)}
              className="w-28 bg-brand-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-400"
              style={{ background: "var(--brand-700, #21262d)" }}
            />
          </div>
          <ActionButton
            label="Dat TP"
            onConfirm={() => api.setDailyTp(parseFloat(tp))}
          />
        </div>
        <div className="flex flex-wrap gap-6 items-end">
          <div className="space-y-1">
            <label className="text-xs text-gray-400">SL trong ngay (USD)</label>
            <input
              type="number" min="0" step="1"
              value={sl}
              onChange={(e) => setSl(e.target.value)}
              className="w-28 bg-brand-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-400"
              style={{ background: "var(--brand-700, #21262d)" }}
            />
          </div>
          <ActionButton
            label="Dat SL"
            onConfirm={() => api.setDailySl(parseFloat(sl))}
          />
        </div>
        <ActionButton
          label="Khoi dong lai sau khi dung TP/SL"
          onConfirm={api.restartEngine}
        />
      </section>

      {/* Chien luoc von */}
      <section className="card space-y-4">
        <h2 className="font-semibold text-gray-200">Chien luoc von</h2>
        <div className="flex flex-wrap gap-6 items-end">
          <div className="space-y-1">
            <label className="text-xs text-gray-400">Chien luoc</label>
            <select
              value={strat}
              onChange={(e) => setStrat(e.target.value)}
              className="border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-400"
              style={{ background: "var(--surface, #161b22)" }}
            >
              {STRATEGIES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <label className="text-xs text-gray-400">Von goc (USD)</label>
            <input
              type="number" min="0.5" step="0.5"
              value={stake}
              onChange={(e) => setStake(e.target.value)}
              className="w-24 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-400"
              style={{ background: "var(--surface, #161b22)" }}
            />
          </div>
          <ActionButton
            label="Ap dung chien luoc"
            onConfirm={() => api.setStrategy(strat, parseFloat(stake))}
          />
        </div>
      </section>
    </div>
  );
}
