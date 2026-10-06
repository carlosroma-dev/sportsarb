import { useEffect, useState, type ReactNode } from "react";
import { useOperations } from "../hooks/useOperations";
import {
  calcFromTotal,
  calcFromStake1,
  calcFromStake2,
  isValidOdd,
  formatBRL,
  formatPct,
  type CalcResult,
} from "../utils/arbCalc";

interface Props {
  onCancel: () => void;
  onSaved: () => void;
}

type LastEdited = "total" | "stake1" | "stake2" | null;

const EMPTY_STAKES = { total: "", stake1: "", stake2: "" };

function Field({
  label,
  optional,
  children,
}: {
  label: string;
  optional?: boolean;
  children: ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs text-muted">
      {label}
      {optional && <span className="text-white/30"> (opcional)</span>}
      {children}
    </label>
  );
}

const inputClass =
  "h-11 w-full rounded-lg border border-white/10 bg-bg px-3 text-sm text-white outline-none transition focus:border-primary/60";

export default function NewOperationModal({ onCancel, onSaved }: Props) {
  const { insert } = useOperations();

  const [eventName, setEventName] = useState("");
  const [marketName, setMarketName] = useState("");
  const [scopeLabel, setScopeLabel] = useState("");
  const [subject, setSubject] = useState("");
  const [line, setLine] = useState("");
  const [kickoff, setKickoff] = useState("");
  const [bookmaker1, setBookmaker1] = useState("");
  const [bookmaker2, setBookmaker2] = useState("");
  const [odd1Raw, setOdd1Raw] = useState("");
  const [odd2Raw, setOdd2Raw] = useState("");
  const [notes, setNotes] = useState("");

  const [stakes, setStakes] = useState(EMPTY_STAKES);
  const [lastEdited, setLastEdited] = useState<LastEdited>(null);

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const odd1 = parseFloat(odd1Raw.replace(",", "."));
  const odd2 = parseFloat(odd2Raw.replace(",", "."));
  const oddsOk = isValidOdd(odd1) && isValidOdd(odd2);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onCancel]);

  let calc: CalcResult | null = null;
  if (oddsOk) {
    const t = parseFloat(stakes.total);
    const s1 = parseFloat(stakes.stake1);
    const s2 = parseFloat(stakes.stake2);
    if (lastEdited === "total" && t > 0) calc = calcFromTotal(t, odd1, odd2);
    else if (lastEdited === "stake1" && s1 > 0) calc = calcFromStake1(s1, odd1, odd2);
    else if (lastEdited === "stake2" && s2 > 0) calc = calcFromStake2(s2, odd1, odd2);
  }

  function onTotalChange(val: string) {
    const t = parseFloat(val);
    if (oddsOk && t > 0) {
      const r = calcFromTotal(t, odd1, odd2);
      setStakes({ total: val, stake1: r.stake1.toFixed(2), stake2: r.stake2.toFixed(2) });
    } else {
      setStakes((f) => ({ ...f, total: val, stake1: "", stake2: "" }));
    }
    setLastEdited("total");
  }

  function onStake1Change(val: string) {
    const s1 = parseFloat(val);
    if (oddsOk && s1 > 0) {
      const r = calcFromStake1(s1, odd1, odd2);
      setStakes({ stake1: val, stake2: r.stake2.toFixed(2), total: r.total.toFixed(2) });
    } else {
      setStakes((f) => ({ ...f, stake1: val, stake2: "", total: "" }));
    }
    setLastEdited("stake1");
  }

  function onStake2Change(val: string) {
    const s2 = parseFloat(val);
    if (oddsOk && s2 > 0) {
      const r = calcFromStake2(s2, odd1, odd2);
      setStakes({ stake2: val, stake1: r.stake1.toFixed(2), total: r.total.toFixed(2) });
    } else {
      setStakes((f) => ({ ...f, stake2: val, stake1: "", total: "" }));
    }
    setLastEdited("stake2");
  }

  const canSave =
    Boolean(eventName.trim()) &&
    Boolean(marketName.trim()) &&
    Boolean(bookmaker1.trim()) &&
    Boolean(bookmaker2.trim()) &&
    oddsOk &&
    calc !== null &&
    calc.stake1 > 0 &&
    calc.stake2 > 0;

  async function handleSubmit() {
    if (!calc) return;
    setSaving(true);
    setError(null);
    try {
      await insert({
        user_id: "local-demo",
        event_name: eventName.trim(),
        market_name: marketName.trim(),
        scope_label: scopeLabel.trim() || null,
        subject: subject.trim() || null,
        line: line.trim() || null,
        kickoff: kickoff.trim() || null,
        bookmaker_1: bookmaker1.trim(),
        bookmaker_2: bookmaker2.trim(),
        odd_1: odd1,
        odd_2: odd2,
        stake_1: parseFloat(calc.stake1.toFixed(2)),
        stake_2: parseFloat(calc.stake2.toFixed(2)),
        total_stake: parseFloat(calc.total.toFixed(2)),
        expected_return: parseFloat(calc.retorno.toFixed(2)),
        profit: parseFloat(calc.lucro.toFixed(2)),
        roi: parseFloat(calc.roi.toFixed(4)),
        status: "concluida",
        notes: notes.trim() || null,
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
      role="dialog"
      aria-modal="true"
      aria-label="Nova operação"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4 py-6 backdrop-blur-sm"
      onClick={(e) => e.target === e.currentTarget && onCancel()}
    >
      <div className="flex max-h-[90vh] w-full max-w-lg flex-col rounded-2xl border border-white/10 bg-card shadow-2xl">
        <div className="flex items-center justify-between border-b border-white/5 px-6 py-4">
          <h2 className="text-lg font-bold">Nova operação</h2>
          <button
            onClick={onCancel}
            aria-label="Fechar"
            className="flex h-9 w-9 items-center justify-center rounded-lg text-muted transition hover:bg-surface/60 hover:text-white"
          >
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Evento">
              <input
                value={eventName}
                onChange={(e) => setEventName(e.target.value)}
                placeholder="Time A x Time B"
                className={inputClass}
              />
            </Field>
            <Field label="Mercado">
              <input
                value={marketName}
                onChange={(e) => setMarketName(e.target.value)}
                placeholder="Total de gols"
                className={inputClass}
              />
            </Field>
            <Field label="Escopo" optional>
              <input
                value={scopeLabel}
                onChange={(e) => setScopeLabel(e.target.value)}
                placeholder="Jogo todo"
                className={inputClass}
              />
            </Field>
            <Field label="Linha" optional>
              <input
                value={line}
                onChange={(e) => setLine(e.target.value)}
                placeholder="2.5"
                className={inputClass}
              />
            </Field>
            <Field label="Referência" optional>
              <input
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                placeholder="Jogador, time..."
                className={inputClass}
              />
            </Field>
            <Field label="Horário do jogo" optional>
              <input
                value={kickoff}
                onChange={(e) => setKickoff(e.target.value)}
                placeholder="22/01 20:00"
                className={inputClass}
              />
            </Field>
          </div>

          <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Casa 1">
              <input
                value={bookmaker1}
                onChange={(e) => setBookmaker1(e.target.value)}
                placeholder="Betano"
                className={inputClass}
              />
            </Field>
            <Field label="Odd 1">
              <input
                value={odd1Raw}
                onChange={(e) => setOdd1Raw(e.target.value)}
                inputMode="decimal"
                placeholder="2.10"
                className={inputClass}
              />
            </Field>
            <Field label="Casa 2">
              <input
                value={bookmaker2}
                onChange={(e) => setBookmaker2(e.target.value)}
                placeholder="Superbet"
                className={inputClass}
              />
            </Field>
            <Field label="Odd 2">
              <input
                value={odd2Raw}
                onChange={(e) => setOdd2Raw(e.target.value)}
                inputMode="decimal"
                placeholder="2.05"
                className={inputClass}
              />
            </Field>
          </div>

          {!oddsOk && (odd1Raw || odd2Raw) && (
            <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2 text-xs text-red-300">
              Odds inválidas — informe dois valores maiores que 1.
            </div>
          )}

          <div className="mb-4 grid grid-cols-3 gap-2">
            <Field label="Total">
              <input
                value={stakes.total}
                onChange={(e) => onTotalChange(e.target.value)}
                inputMode="decimal"
                disabled={!oddsOk}
                placeholder="0,00"
                className={`${inputClass} disabled:opacity-40`}
              />
            </Field>
            <Field label="Stake 1">
              <input
                value={stakes.stake1}
                onChange={(e) => onStake1Change(e.target.value)}
                inputMode="decimal"
                disabled={!oddsOk}
                placeholder="0,00"
                className={`${inputClass} disabled:opacity-40`}
              />
            </Field>
            <Field label="Stake 2">
              <input
                value={stakes.stake2}
                onChange={(e) => onStake2Change(e.target.value)}
                inputMode="decimal"
                disabled={!oddsOk}
                placeholder="0,00"
                className={`${inputClass} disabled:opacity-40`}
              />
            </Field>
          </div>

          {calc && (
            <div className="mb-4 space-y-2 rounded-lg border border-white/5 bg-surface p-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="text-muted">Retorno estimado</span>
                <span className="font-mono font-medium">{formatBRL(calc.retorno)}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted">Lucro</span>
                <span className="font-mono font-bold text-primary">{formatBRL(calc.lucro)}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted">ROI</span>
                <span className="font-mono font-bold text-primary">{formatPct(calc.roi)}</span>
              </div>
            </div>
          )}

          <Field label="Observações" optional>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={2}
              placeholder="Notas sobre a operação..."
              className="w-full resize-none rounded-lg border border-white/10 bg-bg px-3 py-2 text-sm text-white outline-none transition focus:border-primary/60"
            />
          </Field>

          {error && (
            <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2 text-sm text-red-300">
              {error}
            </div>
          )}
        </div>

        <div className="flex gap-3 border-t border-white/5 px-6 py-4">
          <button
            onClick={onCancel}
            disabled={saving}
            className="h-11 flex-1 rounded-lg border border-white/10 text-sm text-muted transition hover:border-white/20 hover:text-white disabled:opacity-40"
          >
            Cancelar
          </button>
          <button
            onClick={handleSubmit}
            disabled={!canSave || saving}
            className="h-11 flex-1 rounded-lg bg-primary text-sm font-semibold text-bg transition hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-40"
          >
            {saving ? "Salvando…" : "Salvar operação"}
          </button>
        </div>
      </div>
    </div>
  );
}
