import { useState } from "react";
import { useOperations } from "../hooks/useOperations";
import ResultSummaryCards from "../components/ResultSummaryCards";
import OperationsTable from "../components/OperationsTable";
import NewOperationModal from "../components/NewOperationModal";
import Spinner from "../components/Spinner";

export default function MeusResultados() {
  const { operations, loading, error, cancel } = useOperations();
  const [cancelling, setCancelling] = useState<string | null>(null);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [newOpOpen, setNewOpOpen] = useState(false);

  async function handleCancel(id: string) {
    setCancelling(id);
    setCancelError(null);
    try {
      await cancel(id);
    } catch (err) {
      setCancelError(err instanceof Error ? err.message : "Erro ao cancelar.");
    } finally {
      setCancelling(null);
    }
  }

  if (loading) return <Spinner />;

  if (error) {
    return (
      <div className="rounded-xl border border-red-500/30 bg-red-500/5 p-6 text-sm text-red-300">
        Erro ao carregar operações: {error}
      </div>
    );
  }

  return (
    <div>
      <div className="mb-4 flex justify-end">
        <button
          onClick={() => setNewOpOpen(true)}
          className="h-11 rounded-lg bg-primary px-4 text-sm font-semibold text-bg transition hover:bg-primary-hover"
        >
          + Nova operação
        </button>
      </div>

      <ResultSummaryCards operations={operations} />

      {cancelError && (
        <div className="mb-4 rounded-xl border border-red-500/30 bg-red-500/5 px-4 py-3 text-sm text-red-300">
          {cancelError}
        </div>
      )}

      <OperationsTable
        operations={operations}
        onCancel={cancelling ? () => {} : handleCancel}
      />

      {newOpOpen && (
        <NewOperationModal
          onCancel={() => setNewOpOpen(false)}
          onSaved={() => setNewOpOpen(false)}
        />
      )}
    </div>
  );
}
