"use client";
/**
 * app/evolution/page.tsx — Client Component
 * Xem champion genome, chay tien hoa, day len cau hinh live.
 */
import { useEffect, useState } from "react";
import ActionButton from "@/components/ActionButton";
import { api } from "@/lib/api";

interface Champion {
  genome_id:          string;
  generation:         number;
  fitness:            number;
  win_rate_pct:       number;
  profit_factor:      number;
  n_trades:           number;
  min_signal_score:   number;
  lookahead_candles:  number;
  wave_weight:        number;
  genes:              Record<string, number>;
}

export default function EvolutionPage() {
  const [champion, setChampion] = useState<Champion | null>(null);
  const [loading,  setLoading]  = useState(true);
  const [runMsg,   setRunMsg]   = useState<string | null>(null);

  async function loadChampion() {
    setLoading(true);
    try {
      const res = await api.evolutionChampion() as { status: string; champion: Champion };
      setChampion(res.champion);
    } catch {
      setChampion(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadChampion(); }, []);

  async function handleRun() {
    setRunMsg("Dang chay…");
    try {
      const res = await api.evolutionRun(5, 10) as { message: string };
      setRunMsg(res.message);
      await loadChampion();
    } catch (e: unknown) {
      setRunMsg(`Loi: ${(e as Error).message}`);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-100">Tien hoa</h1>

      <div className="flex gap-3 flex-wrap items-center">
        <ActionButton label="Chay tien hoa (5 the he)" onConfirm={handleRun} />
        <ActionButton label="Day champion len thuc chien" onConfirm={api.evolutionPromote} />
        {runMsg && <span className="text-xs text-yellow-400">{runMsg}</span>}
      </div>

      {loading && <p className="text-gray-500 text-sm">Dang tai champion…</p>}

      {!loading && !champion && (
        <p className="text-gray-500 text-sm">Chua co champion genome. Hay chay tien hoa truoc.</p>
      )}

      {champion && (
        <div className="card space-y-4">
          <div className="flex items-center gap-3">
            <h2 className="font-semibold text-gray-200">Champion hien tai</h2>
            <span className="badge-ok">the he {champion.generation}</span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <Metric label="Fitness"         value={champion.fitness.toFixed(4)} />
            <Metric label="Ty le thang"     value={`${champion.win_rate_pct.toFixed(1)}%`} />
            <Metric label="He so loi nhuan" value={champion.profit_factor.toFixed(2)} />
            <Metric label="So lenh"         value={String(champion.n_trades)} />
            <Metric label="Diem toi thieu"  value={champion.min_signal_score.toFixed(1)} />
            <Metric label="Tam nhin"        value={`${champion.lookahead_candles} nen`} />
            <Metric label="Trong so song"   value={champion.wave_weight.toFixed(3)} />
            <Metric label="Ma genome"       value={champion.genome_id.slice(0, 12) + "…"} mono />
          </div>
          <div>
            <h3 className="text-xs text-gray-400 uppercase mb-2">Bo gene</h3>
            <div className="grid grid-cols-3 md:grid-cols-5 gap-2">
              {Object.entries(champion.genes).map(([k, v]) => (
                <div key={k} className="bg-gray-800 rounded px-2 py-1">
                  <p className="text-xs text-gray-500 truncate">{k}</p>
                  <p className="text-sm font-mono text-blue-300">{v.toFixed(4)}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <p className="text-xs text-gray-500">{label}</p>
      <p className={`font-semibold text-gray-100 ${mono ? "font-mono text-xs" : ""}`}>{value}</p>
    </div>
  );
}
