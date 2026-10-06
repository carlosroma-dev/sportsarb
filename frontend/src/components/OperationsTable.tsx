import type { Operation } from "../types";
import { formatBRL, formatPct } from "../utils/arbCalc";
import StatusBadge from "./StatusBadge";

interface Props {
  operations: Operation[];
  onCancel: (id: string) => void;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
  });
}

export default function OperationsTable({ operations, onCancel }: Props) {
  if (operations.length === 0) {
    return (
      <div className="rounded-xl border border-white/5 bg-card p-10 text-center text-muted">
        Nenhuma operação registrada ainda. Monte sua primeira entrada no Dashboard.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-white/5">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="border-b border-white/5 bg-surface text-left text-xs font-semibold uppercase tracking-wider text-muted">
            {[
              "Data", "Evento", "Mercado", "Linha",
              "Casa 1", "Odd 1", "Stake 1",
              "Casa 2", "Odd 2", "Stake 2",
              "Total", "Retorno", "Lucro", "ROI",
              "Status", "Ações",
            ].map((h) => (
              <th key={h} className="whitespace-nowrap px-3 py-2.5 sm:px-4 sm:py-3">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {operations.map((op, i) => {
            const cancelled = op.status === "cancelada";
            return (
              <tr
                key={op.id}
                className={`border-b border-white/5 transition ${
                  i % 2 === 0 ? "bg-card" : "bg-surface/40"
                } ${cancelled ? "opacity-50" : ""}`}
              >
                <td className="whitespace-nowrap px-3 py-2.5 text-muted sm:px-4 sm:py-3">{formatDate(op.created_at)}</td>
                <td className="max-w-[160px] truncate px-3 py-2.5 sm:px-4 sm:py-3">{op.event_name}</td>
                <td className="whitespace-nowrap px-3 py-2.5 text-muted sm:px-4 sm:py-3">{op.market_name}</td>
                <td className="px-3 py-2.5 font-mono text-muted sm:px-4 sm:py-3">{op.line ?? "—"}</td>
                <td className="px-3 py-2.5 sm:px-4 sm:py-3">{op.bookmaker_1}</td>
                <td className="px-3 py-2.5 font-mono sm:px-4 sm:py-3">{op.odd_1.toFixed(2)}</td>
                <td className="px-3 py-2.5 font-mono sm:px-4 sm:py-3">{formatBRL(op.stake_1)}</td>
                <td className="px-3 py-2.5 sm:px-4 sm:py-3">{op.bookmaker_2}</td>
                <td className="px-3 py-2.5 font-mono sm:px-4 sm:py-3">{op.odd_2.toFixed(2)}</td>
                <td className="px-3 py-2.5 font-mono sm:px-4 sm:py-3">{formatBRL(op.stake_2)}</td>
                <td className="px-3 py-2.5 font-mono font-medium sm:px-4 sm:py-3">{formatBRL(op.total_stake)}</td>
                <td className="px-3 py-2.5 font-mono sm:px-4 sm:py-3">{formatBRL(op.expected_return)}</td>
                <td className={`px-3 py-2.5 font-mono font-bold sm:px-4 sm:py-3 ${op.profit >= 0 ? "text-primary" : "text-red-400"}`}>
                  {formatBRL(op.profit)}
                </td>
                <td className={`px-3 py-2.5 font-mono font-bold sm:px-4 sm:py-3 ${op.roi >= 0 ? "text-primary" : "text-red-400"}`}>
                  {formatPct(op.roi)}
                </td>
                <td className="px-3 py-2.5 sm:px-4 sm:py-3">
                  <StatusBadge status={op.status} />
                </td>
                <td className="px-3 py-2.5 sm:px-4 sm:py-3">
                  {!cancelled && (
                    <button
                      onClick={() => onCancel(op.id)}
                      className="rounded border border-white/10 px-2.5 py-1.5 text-xs text-muted transition hover:border-red-500/40 hover:text-red-300"
                    >
                      Cancelar
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
