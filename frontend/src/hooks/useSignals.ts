import { useEffect, useRef, useState } from "react";
import { subscribe } from "../services/signalsTransport";
import type { SignalsParams } from "../services/signals";
import type { SignalsResponse } from "../types";

export function useSignals(params: SignalsParams) {
  const [data, setData] = useState<SignalsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);
  const key = JSON.stringify(params);
  const refreshRef = useRef<() => void>(() => {});

  useEffect(() => {
    setLoading(true);
    const stop = subscribe(
      params,
      (r) => {
        setData(r);
        setLoading(false);
        setError(null);
      },
      (e) => {
        setError(e);
        setLoading(false);
      },
    );
    refreshRef.current = () => setNonce((n) => n + 1);
    return stop;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce]);

  useEffect(() => {
    const handler = () => setNonce((n) => n + 1);
    window.addEventListener("signals:refresh", handler);
    return () => window.removeEventListener("signals:refresh", handler);
  }, []);

  return { data, loading, error, refresh: () => refreshRef.current() };
}
