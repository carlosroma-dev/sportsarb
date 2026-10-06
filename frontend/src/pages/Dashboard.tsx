import { useEffect, useMemo, useState } from "react";
import FilterBar, { type FilterValue } from "../components/FilterBar";
import SignalCard from "../components/SignalCard";
import ScanSummaryBar from "../components/ScanSummaryBar";
import Spinner from "../components/Spinner";
import { useSignals } from "../hooks/useSignals";
import { useSettings } from "../hooks/useSettings";
import { useFavorites } from "../hooks/useFavorites";
import { fetchMarkets } from "../services/signals";
import type { MarketOption } from "../types";

export default function Dashboard() {
  const { settings, loading: settingsLoading } = useSettings();
  const favorites = useFavorites();
  const [markets, setMarkets] = useState<MarketOption[]>([{ value: "", label: "Todos os mercados" }]);
  const [filters, setFilters] = useState<FilterValue>({
    minArb: settings.minArb,
    market: "",
    bankroll: settings.bankroll,
  });
  // Preferências locais (min % e banca) chegam da API de forma
  // assíncrona; sincroniza os filtros uma única vez quando o carregamento
  // termina, sem sobrescrever ajustes manuais feitos na FilterBar depois disso.
  const [filtersSyncedWithSettings, setFiltersSyncedWithSettings] = useState(false);

  useEffect(() => {
    fetchMarkets().then(setMarkets).catch(() => {});
  }, []);

  useEffect(() => {
    if (!settingsLoading && !filtersSyncedWithSettings) {
      setFilters((f) => ({ ...f, minArb: settings.minArb, bankroll: settings.bankroll }));
      setFiltersSyncedWithSettings(true);
    }
  }, [settingsLoading, filtersSyncedWithSettings, settings]);

  const params = useMemo(
    () => ({ minArb: filters.minArb, market: filters.market, bankroll: filters.bankroll }),
    [filters],
  );
  const { data, loading } = useSignals(params);
  const signals = data?.signals ?? [];

  return (
    <div>
      <ScanSummaryBar data={data} signalCount={signals.length} />
      <FilterBar value={filters} marketOptions={markets} onApply={setFilters} />
      {loading && !data ? (
        <Spinner />
      ) : signals.length === 0 ? (
        <div className="rounded-xl border border-white/5 bg-card p-10 text-center text-muted">
          Nenhum sinal para o filtro atual. Reduza a margem mínima ou troque o mercado.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {signals.map((s) => (
            <SignalCard
              key={s.signal_id}
              signal={s}
              favorite={favorites.has(s.signal_id)}
              onToggleFavorite={favorites.toggle}
            />
          ))}
        </div>
      )}
    </div>
  );
}
