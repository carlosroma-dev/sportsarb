import { useEffect, useState } from "react";
import { useSettings } from "../hooks/useSettings";
import { fetchBookmakers } from "../services/signals";
import type { BookmakerOption } from "../types";

export default function Configuracoes() {
  const { settings, loading, error, saving, saveError, saved, save } = useSettings();
  const [draft, setDraft] = useState(settings);
  const [bookmakers, setBookmakers] = useState<BookmakerOption[]>([]);

  useEffect(() => setDraft(settings), [settings]);

  useEffect(() => {
    fetchBookmakers().then(setBookmakers).catch(() => {});
  }, []);

  const toggleBookmaker = (id: string) => {
    setDraft((d) => ({
      ...d,
      excludedBookmakers: d.excludedBookmakers.includes(id)
        ? d.excludedBookmakers.filter((b) => b !== id)
        : [...d.excludedBookmakers, id],
    }));
  };

  if (loading) {
    return <div className="max-w-lg text-sm text-muted">Carregando preferências…</div>;
  }

  return (
    <div className="max-w-lg">
      <h2 className="mb-4 text-lg font-bold">Configurações</h2>
      {error && (
        <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-400">
          Erro ao carregar preferências: {error}
        </div>
      )}
      <div className="flex flex-col gap-4 rounded-xl border border-line bg-card p-6">
        <label className="flex flex-col gap-1 text-sm text-muted">
          Min % padrão
          <input
            value={draft.minArb}
            onChange={(e) => setDraft({ ...draft, minArb: e.target.value })}
            className="rounded-lg border border-line bg-bg px-3 py-2 text-white outline-none focus:border-primary"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm text-muted">
          Banca padrão
          <input
            value={draft.bankroll}
            onChange={(e) => setDraft({ ...draft, bankroll: e.target.value })}
            className="rounded-lg border border-line bg-bg px-3 py-2 text-white outline-none focus:border-primary"
          />
        </label>

        <div className="flex flex-col gap-2 text-sm text-muted">
          Casas vetadas (fora do cálculo de arbitragem)
          <div className="flex flex-col gap-2 rounded-lg border border-line bg-bg p-3">
            {bookmakers.map((b) => (
              <label key={b.id} className="flex items-center gap-2 text-white">
                <input
                  type="checkbox"
                  checked={draft.excludedBookmakers.includes(b.id)}
                  onChange={() => toggleBookmaker(b.id)}
                />
                {b.label}
              </label>
            ))}
          </div>
        </div>

        <button
          onClick={() => save(draft)}
          disabled={saving}
          className="self-start rounded-lg bg-primary px-5 py-2 font-semibold text-bg transition hover:bg-primary-hover disabled:opacity-60"
        >
          {saving ? "Salvando…" : "Salvar"}
        </button>
        {saveError && <span className="text-sm text-red-400">Erro ao salvar: {saveError}</span>}
        {saved && !saveError && <span className="text-sm text-primary">Salvo!</span>}
      </div>
    </div>
  );
}
