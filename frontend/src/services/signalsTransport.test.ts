import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

vi.mock("./signals", () => ({ fetchSignals: vi.fn() }));
import * as signalsApi from "./signals";
import { subscribe } from "./signalsTransport";

describe("signalsTransport (polling)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("faz fetch imediato e repete a cada 30s; unsubscribe para o polling", async () => {
    const resp = { updated_at: null, competition: null, collected_by_house: {}, signals: [] };
    vi.mocked(signalsApi.fetchSignals).mockResolvedValue(resp as never);
    const onData = vi.fn();
    const stop = subscribe({ minArb: "1", bankroll: "1000" }, onData);

    await vi.advanceTimersByTimeAsync(0);
    expect(signalsApi.fetchSignals).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(30_000);
    expect(signalsApi.fetchSignals).toHaveBeenCalledTimes(2);

    stop();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(signalsApi.fetchSignals).toHaveBeenCalledTimes(2);
  });
});
