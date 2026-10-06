import { fetchSignals, type SignalsParams } from "./signals";
import type { SignalsResponse } from "../types";

const POLL_MS = 30_000;

/**
 * Abstrai a fonte de sinais. Hoje = polling HTTP. Para trocar por WebSocket/SSE,
 * reescreva apenas esta função mantendo a assinatura — os componentes não mudam.
 */
export function subscribe(
  params: SignalsParams,
  onData: (r: SignalsResponse) => void,
  onError?: (e: unknown) => void,
): () => void {
  let stopped = false;

  const tick = async () => {
    try {
      const data = await fetchSignals(params);
      if (!stopped) onData(data);
    } catch (e) {
      if (!stopped && onError) onError(e);
    }
  };

  void tick();
  const id = setInterval(tick, POLL_MS);

  return () => {
    stopped = true;
    clearInterval(id);
  };
}
