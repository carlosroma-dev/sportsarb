import { useState, useCallback } from "react";
import type { SignalDTO } from "../types";
import {
  calcFromTotal,
  calcFromStake1,
  calcFromStake2,
  isValidOdd,
  formatBRL,
  formatPct,
  type CalcResult,
} from "../utils/arbCalc";
import SaveOperationModal from "./SaveOperationModal";

interface Props {
  signal: SignalDTO;
  onClose: () => void;
}

type LastEdited = "total" | "stake1" | "stake2" | null;

const EMPTY = { total: "", stake1: "", stake2: "" };

function ResultRow({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted">{label}</span>
      <span className={highlight ? "font-bold text-primary" : "font-mono font-medium"}>{value}</span>
    </div>
  );
}

export default function EntryCalculator({ signal, onClose }: Props) {
  const odd1 = parseFloat(signal.over_odd);
  const odd2 = parseFloat(signal.under_odd);
  const oddsOk = isValidOdd(odd1) && isValidOdd(odd2);

  const [fields, setFields] = useState(EMPTY);
  const [lastEdited, setLastEdited] = useState<LastEdited>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const calc: CalcResult | null = useCallback((): CalcResult | null => {
    if (!oddsOk) return null;
    const t = parseFloat(fields.total);
    const s1 = parseFloat(fields.stake1);
    const s2 = parseFloat(fields.stake2);

    if (lastEdited === "total" && t > 0) return calcFromTotal(t, odd1, odd2);
    if (lastEdited === "stake1" && s1 > 0) return calcFromStake1(s1, odd1, odd2);
    if (lastEdited === "stake2" && s2 > 0) return calcFromStake2(s2, odd1, odd2);
    return null;
  }, [fields, lastEdited, odd1, odd2, oddsOk])();

  function onTotalChange(val: string) {
    const t = parseFloat(val);
    if (oddsOk && t > 0) {
      const r = calcFromTotal(t, odd1, odd2);
      setFields({ total: val, stake1: r.stake1.toFixed(2), stake2: r.stake2.toFixed(2) });
    } else {
      setFields((f) => ({ ...f, total: val, stake1: "", stake2: "" }));
    }
    setLastEdited("total");
  }

  function onStake1Change(val: string) {
    const s1 = parseFloat(val);
    if (oddsOk && s1 > 0) {
      const r = calcFromStake1(s1, odd1, odd2);
      setFields({ stake1: val, stake2: r.stake2.toFixed(2), total: r.total.toFixed(2) });
    } else {
      setFields((f) => ({ ...f, stake1: val, stake2: "", total: "" }));
    }
    setLastEdited("stake1");
  }

  function onStake2Change(val: string) {
    const s2 = parseFloat(val);
    if (oddsOk && s2 > 0) {
      const r = calcFromStake2(s2, odd1, odd2);
      setFields({ stake2: val, stake1: r.stake1.toFixed(2), total: r.total.toFixed(2) });
    } else {
      setFields((f) => ({ ...f, stake2: val, stake1: "", total: "" }));
    }
    setLastEdited("stake2");
  }

  function handleClear() {
    setFields(EMPTY);
    setLastEdited(null);
  }

  const canSave = oddsOk && calc !== null && calc.stake1 > 0 && calc.stake2 > 0;

  return (
    <div className="p-4">
      {!oddsOk && (
        <div className="mb-3 rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2 text-xs text-red-300">
          Odds inválidas — não é possível calcular.
        </div>
      )}

      {/* Inputs */}
      <div className="mb-4 grid grid-cols-3 gap-2">
        <div className="flex flex-col gap-1">
          <label htmlFor={`total-${signal.signal_id}`} className="text-[10px] font-semibold uppercase tracking-wider text-muted">
            Total
          </label>
          <input
            id={`total-${signal.signal_id}`}
            aria-label="Total"
            type="number"
            min="0"
            step="any"
            placeholder="0,00"
            value={fields.total}
            onChange={(e) => onTotalChange(e.target.value)}
            disabled={!oddsOk}
            className="w-full rounded-lg border border-white/10 bg-bg px-2.5 py-2 text-sm outline-none transition focus:border-primary/60 disabled:opacity-40"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={`stake1-${signal.signal_id}`} className="text-[10px] font-semibold uppercase tracking-wider text-muted">
            Stake OVER
          </label>
          <input
            id={`stake1-${signal.signal_id}`}
            aria-label="Stake OVER"
            type="number"
            min="0"
            step="any"
            placeholder="0,00"
            value={fields.stake1}
            onChange={(e) => onStake1Change(e.target.value)}
            disabled={!oddsOk}
            className="w-full rounded-lg border border-white/10 bg-bg px-2.5 py-2 text-sm outline-none transition focus:border-primary/60 disabled:opacity-40"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={`stake2-${signal.signal_id}`} className="text-[10px] font-semibold uppercase tracking-wider text-muted">
            Stake UNDER
          </label>
          <input
            id={`stake2-${signal.signal_id}`}
            aria-label="Stake UNDER"
            type="number"
            min="0"
            step="any"
            placeholder="0,00"
            value={fields.stake2}
            onChange={(e) => onStake2Change(e.target.value)}
            disabled={!oddsOk}
            className="w-full rounded-lg border border-white/10 bg-bg px-2.5 py-2 text-sm outline-none transition focus:border-primary/60 disabled:opacity-40"
          />
        </div>
      </div>

      {/* Resultado */}
      {calc && (
        <div className="mb-4 rounded-lg border border-white/5 bg-surface p-3 space-y-2">
          <ResultRow label="Stake total" value={formatBRL(calc.total)} />
          <ResultRow label="Stake OVER" value={formatBRL(calc.stake1)} />
          <ResultRow label="Stake UNDER" value={formatBRL(calc.stake2)} />
          <div className="border-t border-white/5 pt-2 space-y-2">
            <ResultRow label="Retorno estimado" value={formatBRL(calc.retorno)} />
            <ResultRow label="Lucro" value={formatBRL(calc.lucro)} highlight />
            <ResultRow label="ROI" value={formatPct(calc.roi)} highlight />
          </div>
        </div>
      )}

      {/* Botões */}
      <div className="flex gap-2">
        <button
          onClick={() => setModalOpen(true)}
          disabled={!canSave}
          className="flex-1 rounded-lg bg-primary py-2 text-sm font-semibold text-bg transition hover:bg-primary-hover disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Salvar operação
        </button>
        <button
          onClick={handleClear}
          className="rounded-lg border border-white/10 px-4 py-2 text-sm text-muted transition hover:border-white/20 hover:text-white"
        >
          Limpar
        </button>
      </div>

      {/* Modal de confirmação */}
      {modalOpen && calc && (
        <SaveOperationModal
          signal={signal}
          calc={calc}
          onCancel={() => setModalOpen(false)}
          onSaved={() => {
            setModalOpen(false);
            handleClear();
            onClose();
          }}
        />
      )}
    </div>
  );
}
