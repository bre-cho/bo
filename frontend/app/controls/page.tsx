"use client";
/**
 * app/controls/page.tsx — Client Component
 * Engine controls, TP/SL, strategy switcher.
 * Requires X-API-Key header (set in .env.local).
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
      <h1 className="text-2xl font-bold text-gray-100">Controls</h1>

      {/* Engine */}
      <section className="card space-y-4">
        <h2 className="font-semibold text-gray-200">Engine Mode</h2>
        <div className="flex flex-wrap gap-3">
          <ActionButton label="Pause (→ PAPER)" onConfirm={api.enginePause} />
          <ActionButton label="Resume (→ LIVE)"  onConfirm={api.engineResume} />
          <ActionButton label="Stop"             onConfirm={api.engineStop} variant="danger" />
        </div>
        <p className="text-xs text-gray-500">
          Pause switches to PAPER mode (no real trades). Stop halts the engine entirely.
        </p>
      </section>

      {/* Daily TP / SL */}
      <section className="card space-y-4">
        <h2 className="font-semibold text-gray-200">Daily Take-Profit / Stop-Loss (USD)</h2>
        <div className="flex flex-wrap gap-6 items-end">
          <div className="space-y-1">
            <label className="text-xs text-gray-400">Daily TP (USD)</label>
            <input
              type="number" min="0" step="1"
              value={tp}
              onChange={(e) => setTp(e.target.value)}
              className="w-28 bg-brand-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-400"
              style={{ background: "var(--brand-700, #21262d)" }}
            />
          </div>
          <ActionButton
            label="Set TP"
            onConfirm={() => api.setDailyTp(parseFloat(tp))}
          />
        </div>
        <div className="flex flex-wrap gap-6 items-end">
          <div className="space-y-1">
            <label className="text-xs text-gray-400">Daily SL (USD)</label>
            <input
              type="number" min="0" step="1"
              value={sl}
              onChange={(e) => setSl(e.target.value)}
              className="w-28 bg-brand-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-400"
              style={{ background: "var(--brand-700, #21262d)" }}
            />
          </div>
          <ActionButton
            label="Set SL"
            onConfirm={() => api.setDailySl(parseFloat(sl))}
          />
        </div>
        <ActionButton
          label="Restart after TP/SL stop"
          onConfirm={api.restartEngine}
        />
      </section>

      {/* Strategy */}
      <section className="card space-y-4">
        <h2 className="font-semibold text-gray-200">Capital Strategy</h2>
        <div className="flex flex-wrap gap-6 items-end">
          <div className="space-y-1">
            <label className="text-xs text-gray-400">Strategy</label>
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
            <label className="text-xs text-gray-400">Base Stake (USD)</label>
            <input
              type="number" min="0.5" step="0.5"
              value={stake}
              onChange={(e) => setStake(e.target.value)}
              className="w-24 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-400"
              style={{ background: "var(--surface, #161b22)" }}
            />
          </div>
          <ActionButton
            label="Apply Strategy"
            onConfirm={() => api.setStrategy(strat, parseFloat(stake))}
          />
        </div>
      </section>
    </div>
  );
}
