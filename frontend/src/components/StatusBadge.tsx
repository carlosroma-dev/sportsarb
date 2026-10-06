import type { OperationStatus } from "../types";

interface Props {
  status: OperationStatus;
}

export default function StatusBadge({ status }: Props) {
  if (status === "concluida") {
    return (
      <span className="inline-flex items-center rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">
        Concluída
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-xs font-semibold text-muted line-through">
      Cancelada
    </span>
  );
}
