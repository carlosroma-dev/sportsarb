import { useCallback, useEffect, useState } from "react";
import { api } from "../services/api";
import type { NewOperation, Operation } from "../types";

export function useOperations() {
  const [operations, setOperations] = useState<Operation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get<Operation[]>("/api/v1/operations");
      setOperations(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao carregar operações");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  const insert = useCallback(async (op: NewOperation) => {
    await api.post("/api/v1/operations", op);
    await fetchAll();
  }, [fetchAll]);

  const cancel = useCallback(async (id: string) => {
    await api.patch(`/api/v1/operations/${id}/cancel`);
    setOperations((prev) =>
      prev.map((op) => (op.id === id ? { ...op, status: "cancelada" } : op)),
    );
  }, []);

  return { operations, loading, error, insert, cancel, refetch: fetchAll };
}
