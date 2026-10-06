import SignalCard from "../components/SignalCard";
import { useFavorites } from "../hooks/useFavorites";
import { useSignals } from "../hooks/useSignals";
import { useSettings } from "../hooks/useSettings";

export default function Favoritos() {
  const { settings } = useSettings();
  const favorites = useFavorites();
  const { data } = useSignals({ minArb: "0", bankroll: settings.bankroll });
  const items = (data?.signals ?? []).filter((s) => favorites.has(s.signal_id));

  return (
    <div>
      <h2 className="mb-4 text-lg font-bold">Favoritos</h2>
      {items.length === 0 ? (
        <div className="rounded-xl border border-line bg-card p-10 text-center text-muted">
          Você ainda não marcou sinais. Toque na ★ de um card no Dashboard.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {items.map((s) => (
            <SignalCard key={s.signal_id} signal={s} favorite onToggleFavorite={favorites.toggle} />
          ))}
        </div>
      )}
    </div>
  );
}
