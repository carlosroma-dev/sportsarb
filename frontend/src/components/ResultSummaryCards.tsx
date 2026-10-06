import type { Operation } from "../types";
import { formatBRL, formatPct } from "../utils/arbCalc";

interface Props {
  operations: Operation[];
}

function isCurrentMonth(iso: string): boolean {
  const d = new Date(iso);
  const now = new Date();
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth();
}

interface CardProps {
  label: string;
  value: string;
  accent?: boolean;
  sub?: string;
}

function SummaryCard({ label, value, accent, sub }: CardProps) {
  return (
    <div className="flex flex-col gap-1.5 rounded-xl border border-white/5 bg-surface px-5 py-4">
      <span className="text-xs font-medium text-muted">{label}</span>
      <span className={`text-2xl font-bold tabular-nums ${accent ? "text-primary" : "text-white"}`}>
        {value}
      </span>
      {sub && <span className="text-xs text-muted">{sub}</span>}
    </div>
  );
}

export default function ResultSummaryCards({ operations }: Props) {
  const monthOps = operations.filter(
    (op) => op.status === "concluida" && isCurrentMonth(op.created_at),
  );

  const lucroMes = monthOps.reduce((acc, op) => acc + op.profit, 0);
  const roiMedio = monthOps.length > 0
    ? monthOps.reduce((acc, op) => acc + op.roi, 0) / monthOps.length
    : 0;
  const volumeMes = monthOps.reduce((acc, op) => acc + op.total_stake, 0);
  const melhorRoi = monthOps.length > 0 ? Math.max(...monthOps.map((op) => op.roi)) : 0;

  const now = new Date();
  const mesLabel = now.toLocaleDateString("pt-BR", { month: "long", year: "numeric" });

  return (
    <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-5">
      <SummaryCard
        label={`Lucro — ${mesLabel}`}
        value={formatBRL(lucroMes)}
        accent={lucroMes >= 0}
        sub={`${monthOps.length} operações`}
      />
      <SummaryCard
        label="ROI médio do mês"
        value={formatPct(roiMedio)}
        accent={roiMedio >= 0}
      />
      <SummaryCard
        label="Operações no mês"
        value={String(monthOps.length)}
      />
      <SummaryCard
        label="Volume apostado"
        value={formatBRL(volumeMes)}
      />
      <SummaryCard
        label="Melhor ROI"
        value={melhorRoi > 0 ? formatPct(melhorRoi) : "—"}
        accent={melhorRoi > 0}
      />
    </div>
  );
}
