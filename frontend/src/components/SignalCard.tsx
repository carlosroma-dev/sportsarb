import { useState } from "react";
import type { SignalDTO } from "../types";
import EntryCalculator from "./EntryCalculator";

interface Props {
  signal: SignalDTO;
  favorite: boolean;
  onToggleFavorite: (id: string) => void;
}

interface LegProps {
  side: string;
  odd: string;
  house: string;
  url: string | null;
}

function Leg({ side, odd, house, url }: LegProps) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-white/5 bg-bg/60 px-3 py-2.5">
      <span className="text-[10px] font-bold uppercase tracking-widest text-muted">{side}</span>
      <span className="font-mono text-base font-bold">{odd}</span>
      {url ? (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-muted hover:text-primary transition"
        >
          {house} ↗
        </a>
      ) : (
        <span className="text-xs text-muted">{house}</span>
      )}
    </div>
  );
}

export default function SignalCard({ signal: s, favorite, onToggleFavorite }: Props) {
  const [calcOpen, setCalcOpen] = useState(false);
  const roi = Number(s.profit_pct);

  return (
    <article
      className={`rounded-xl border bg-card transition ${
        s.highlight
          ? "border-primary/40 shadow-neon"
          : "border-white/5 hover:border-white/10"
      }`}
    >
      <div className="p-4">
        {/* Header */}
        <div className="mb-3 flex items-start gap-3">
          <span className="shrink-0 rounded-lg border border-primary/30 bg-primary/10 px-2.5 py-1 font-mono text-lg font-bold text-primary">
            +{roi.toFixed(2)}%
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate font-semibold leading-tight">{s.match}</div>
            <div className="mt-0.5 text-xs text-muted">{s.metric_label} · {s.scope_label}</div>
          </div>
          <button
            onClick={() => onToggleFavorite(s.signal_id)}
            aria-label={favorite ? "Remover dos favoritos" : "Favoritar"}
            aria-pressed={favorite}
            className={`-m-2.5 flex h-11 w-11 shrink-0 items-center justify-center text-lg leading-none transition ${favorite ? "text-gold" : "text-muted hover:text-gold"}`}
          >
            ★
          </button>
        </div>

        {/* Settlement warning */}
        {s.settlement_warning && (
          <div className="mb-3 inline-flex items-center gap-1.5 rounded-md border border-amber/30 bg-amber/10 px-2 py-1 text-xs font-semibold text-amber">
            ⚠ conferir settlement
          </div>
        )}

        {/* Meta */}
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted">
          {s.subject && (
            <span className="rounded-full border border-white/10 bg-surface px-2 py-0.5 font-medium text-white/70">
              {s.subject}
            </span>
          )}
          {s.line && (
            <span className="rounded-md border border-white/10 bg-surface px-2 py-0.5 font-mono">
              linha <span className="font-bold text-white">{s.line}</span>
            </span>
          )}
          <span className="ml-auto font-mono">{s.kickoff}</span>
        </div>

        {/* Pernas */}
        <div className="grid grid-cols-2 gap-2">
          <Leg
            side={s.over_label || "over"}
            odd={s.over_odd}
            house={s.over_bookmaker}
            url={s.over_url}
          />
          <Leg
            side={s.under_label || "under"}
            odd={s.under_odd}
            house={s.under_bookmaker}
            url={s.under_url}
          />
        </div>
      </div>

      {/* Botão Montar entrada */}
      <div className="border-t border-white/5 px-4 py-2.5">
        <button
          onClick={() => setCalcOpen((o) => !o)}
          className="w-full rounded-lg border border-white/10 py-2 text-sm font-medium text-muted transition hover:border-primary/40 hover:text-primary"
        >
          {calcOpen ? "Fechar calculadora" : "Montar entrada"}
        </button>
      </div>

      {/* EntryCalculator inline */}
      {calcOpen && (
        <div className="border-t border-white/5">
          <EntryCalculator
            signal={s}
            onClose={() => setCalcOpen(false)}
          />
        </div>
      )}
    </article>
  );
}
