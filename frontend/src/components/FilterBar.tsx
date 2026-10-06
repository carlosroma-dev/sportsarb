import { useState } from "react";
import type { MarketOption } from "../types";

export interface FilterValue {
  minArb: string;
  market: string;
  bankroll: string;
}

interface Props {
  value: FilterValue;
  marketOptions: MarketOption[];
  onApply: (v: FilterValue) => void;
}

export default function FilterBar({ value, marketOptions, onApply }: Props) {
  const [draft, setDraft] = useState<FilterValue>(value);
  return (
    <div className="mb-6 flex flex-col gap-3 rounded-xl border border-line bg-card p-4 sm:flex-row sm:flex-wrap sm:items-end">
      <label className="flex flex-col gap-1 text-xs text-muted sm:w-24">
        Min %
        <input
          value={draft.minArb}
          onChange={(e) => setDraft({ ...draft, minArb: e.target.value })}
          inputMode="decimal"
          className="h-11 w-full rounded-lg border border-line bg-bg px-3 text-sm text-white outline-none focus:border-primary"
        />
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted sm:min-w-[180px] sm:flex-1">
        Mercado
        <select
          value={draft.market}
          onChange={(e) => setDraft({ ...draft, market: e.target.value })}
          className="h-11 w-full rounded-lg border border-line bg-bg px-3 text-sm text-white outline-none focus:border-primary"
        >
          {marketOptions.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted sm:w-28">
        Banca
        <input
          value={draft.bankroll}
          onChange={(e) => setDraft({ ...draft, bankroll: e.target.value })}
          inputMode="numeric"
          className="h-11 w-full rounded-lg border border-line bg-bg px-3 text-sm text-white outline-none focus:border-primary"
        />
      </label>
      <button
        onClick={() => onApply(draft)}
        className="h-11 w-full rounded-lg bg-primary px-5 text-sm font-semibold text-bg transition hover:bg-primary-hover sm:w-auto"
      >
        Aplicar
      </button>
    </div>
  );
}
