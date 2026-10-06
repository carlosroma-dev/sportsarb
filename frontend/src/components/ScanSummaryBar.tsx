import type { SignalsResponse } from "../types";

interface Props {
  data: SignalsResponse | null;
  signalCount: number;
}

interface MetricCardProps {
  label: string;
  value: string;
  accent?: boolean;
}

function MetricCard({ label, value, accent }: MetricCardProps) {
  return (
    <div className="flex flex-col gap-1 rounded-xl border border-white/5 bg-surface px-4 py-3">
      <span className="text-xs text-muted">{label}</span>
      <span className={`text-lg font-bold ${accent ? "text-primary" : "text-white"}`}>{value}</span>
    </div>
  );
}

function formatUpdatedAt(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

function bestRoi(signals: SignalsResponse["signals"]): string {
  if (!signals.length) return "—";
  const max = Math.max(...signals.map((s) => parseFloat(s.profit_pct)));
  return `+${max.toFixed(2)}%`;
}

export default function ScanSummaryBar({ data, signalCount }: Props) {
  const casasOnline = data ? Object.keys(data.collected_by_house).length : 0;

  return (
    <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
      <MetricCard label="Oportunidades ativas" value={String(signalCount)} accent />
      <MetricCard label="Maior ROI" value={bestRoi(data?.signals ?? [])} accent />
      <MetricCard label="Demo gerada" value={formatUpdatedAt(data?.updated_at ?? null)} />
      <MetricCard label="Fontes simuladas" value={String(casasOnline)} />
    </div>
  );
}
