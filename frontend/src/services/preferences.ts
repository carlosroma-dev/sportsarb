import { api } from "./api";
import type { UserPreferences } from "../types";

export async function fetchPreferences(): Promise<UserPreferences> {
  const { data } = await api.get<UserPreferences>("/api/v1/preferences");
  return { ...data, minArb: String(data.minArb), bankroll: String(data.bankroll) };
}

export async function savePreferences(prefs: UserPreferences): Promise<void> {
  await api.put("/api/v1/preferences", prefs);
}
