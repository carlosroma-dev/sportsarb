import { useState } from "react";
import { useOperations } from "../hooks/useOperations";
import { formatBRL, formatPct, type CalcResult } from "../utils/arbCalc";
import type { SignalDTO } from "../types";

interface Props {
  signal: SignalDTO;
  calc: CalcResult;
  onCancel: () => void;
  onSaved: () => void;
}

export default function SaveOperationModal({ signal, calc, onCancel, onSaved }: Props) {
  const { insert } = useOperations();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleConfirm() {
    setSaving(true);
    setError(null);
    try {
      await insert({
        user_id: "local-demo",
        event_name: signal.match,
        market_name: signal.metric_label,
        scope_label: signal.scope_label,
        subject: signal.subject,
        line: signal.line,
        kickoff: signal.kickoff,
        bookmaker_1: signal.over_bookmaker,
        bookmaker_2: signal.under_bookmaker,
        odd_1: parseFloat(signal.over_odd),
        odd_2: parseFloat(signal.under_odd),
        stake_1: parseFloat(calc.stake1.toFixed(2)),
        stake_2: parseFloat(calc.stake2.toFixed(2)),
        total_stake: parseFloat(calc.total.toFixed(2)),
        expected_return: parseFloat(calc.retorno.toFixed(2)),
        profit: parseFloat(calc.lucro.toFixed(2)),
        roi: parseFloat(calc.roi.toFixed(4)),
        status: "concluida",
        notes: null,
      });
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao salvar operação.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4 backdrop-blur-sm"
      onClick={(e) => e.target === e.currentTarget && onCancel()}
    >
      <div className="w-full max-w-md rounded-2xl border border-white/10 bg-card p-6 shadow-2xl">
        <h2 className="mb-1 text-lg font-bold">Confirmar operação</h2>
        <p className="mb-5 text-sm text-muted">Revise os valores antes de salvar.</p>

        <div className="mb-5 space-y-3 rounded-xl border border-white/5 bg-surface p-4 text-sm">
          <div className="flex justify-between">
            <span className="text-muted">Evento</span>
            <span className="font-medium text-right max-w-[60%]">{signal.match}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted">Mercado</span>
            <span>{signal.metric_label} · linha {signal.line}</span>
          </div>
          <div className="border-t border-white/5 pt-3 space-y-2">
            <div className="flex justify-between">
              <span className="text-muted">OVER — {signal.over_bookmaker} @ {signal.over_odd}</span>
              <span className="font-mono font-medium">{formatBRL(calc.stake1)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">UNDER — {signal.under_bookmaker} @ {signal.under_odd}</span>
              <span className="font-mono font-medium">{formatBRL(calc.stake2)}</span>
            </div>
          </div>
          <div className="border-t border-white/5 pt-3 space-y-2">
            <div className="flex justify-between">
              <span className="text-muted">Total apostado</span>
              <span className="font-mono font-bold">{formatBRL(calc.total)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">Retorno estimado</span>
              <span className="font-mono">{formatBRL(calc.retorno)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">Lucro</span>
              <span className="font-mono font-bold text-primary">{formatBRL(calc.lucro)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">ROI</span>
              <span className="font-mono font-bold text-primary">{formatPct(calc.roi)}</span>
            </div>
          </div>
        </div>

        {error && (
          <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2 text-sm text-red-300">
            {error}
          </div>
        )}

        <div className="flex gap-3">
          <button
            onClick={onCancel}
            disabled={saving}
            className="flex-1 rounded-lg border border-white/10 py-2.5 text-sm text-muted transition hover:border-white/20 hover:text-white disabled:opacity-40"
          >
            Cancelar
          </button>
          <button
            onClick={handleConfirm}
            disabled={saving}
            className="flex-1 rounded-lg bg-primary py-2.5 text-sm font-semibold text-bg transition hover:bg-primary-hover disabled:opacity-60"
          >
            {saving ? "Salvando…" : "Confirmar e salvar"}
          </button>
        </div>
      </div>
    </div>
  );
}
