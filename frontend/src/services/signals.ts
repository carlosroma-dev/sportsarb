import { api } from "./api";
import type { BookmakerOption, MarketOption, SignalsResponse } from "../types";

export interface SignalsParams {
  minArb?: string;
  market?: string;
  bankroll?: string;
  competition?: string;
}

export async function fetchSignals(params: SignalsParams): Promise<SignalsResponse> {
  const { data } = await api.get<SignalsResponse>("/api/v1/signals", {
    params: {
      min_arb: params.minArb,
      market: params.market || undefined,
      bankroll: params.bankroll,
      competition: params.competition,
    },
  });
  return data;
}

export async function fetchMarkets(): Promise<MarketOption[]> {
  const { data } = await api.get<MarketOption[]>("/api/v1/markets");
  return data;
}

export async function fetchBookmakers(): Promise<BookmakerOption[]> {
  const { data } = await api.get<BookmakerOption[]>("/api/v1/bookmakers");
  return data;
}
