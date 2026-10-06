import { useCallback, useEffect, useState } from "react";
import { fetchPreferences, savePreferences } from "../services/preferences";
import type { UserPreferences } from "../types";

export type Settings = UserPreferences;

export const DEFAULT_SETTINGS: Settings = {
  minArb: "3",
  bankroll: "1000",
  excludedBookmakers: [],
};

export function useSettings() {
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    fetchPreferences()
      .then((prefs) => {
        if (active) setSettings(prefs ?? DEFAULT_SETTINGS);
      })
      .catch((e: unknown) => {
        if (active) setError(e instanceof Error ? e.message : "Erro ao carregar preferências");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const save = useCallback(
    async (patch: Partial<Settings>) => {
      const next = { ...settings, ...patch };
      setSaving(true);
      setSaveError(null);
      setSaved(false);
      try {
        await savePreferences(next);
        setSettings(next);
        setSaved(true);
        window.dispatchEvent(new Event("signals:refresh"));
      } catch (e) {
        setSaveError(e instanceof Error ? e.message : "Erro ao salvar preferências");
        throw e;
      } finally {
        setSaving(false);
      }
    },
    [settings],
  );

  return { settings, loading, error, saving, saveError, saved, save };
}
