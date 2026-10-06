export interface SignalDTO {
  signal_id: string;
  match: string;
  metric: string;
  metric_label: string;
  scope_label: string;
  subject: string | null;
  period_label: string;
  line: string;
  kickoff: string;
  profit_pct: string;
  over_bookmaker: string;
  over_odd: string;
  over_url: string | null;
  over_stake: string;
  under_bookmaker: string;
  under_odd: string;
  under_url: string | null;
  under_stake: string;
  settlement_warning: boolean;
  highlight: boolean;
  // Rótulos das pernas: futebol usa over/under; no vencedor de tênis cada
  // perna é um jogador. Opcionais para compatibilidade com payloads antigos.
  over_label?: string;
  under_label?: string;
}

export interface SignalsResponse {
  updated_at: string | null;
  competition: string | null;
  collected_by_house: Record<string, number>;
  signals: SignalDTO[];
}

export interface MarketOption {
  value: string;
  label: string;
}

export interface BookmakerOption {
  id: string;
  label: string;
}

export interface UserPreferences {
  minArb: string;
  bankroll: string;
  excludedBookmakers: string[];
}

export type OperationStatus = "concluida" | "cancelada";

export interface Operation {
  id: string;
  user_id: string;
  created_at: string;
  event_name: string;
  market_name: string;
  scope_label: string | null;
  subject: string | null;
  line: string | null;
  kickoff: string | null;
  bookmaker_1: string;
  bookmaker_2: string;
  odd_1: number;
  odd_2: number;
  stake_1: number;
  stake_2: number;
  total_stake: number;
  expected_return: number;
  profit: number;
  roi: number;
  status: OperationStatus;
  notes: string | null;
}

export type NewOperation = Omit<Operation, "id" | "created_at">;
