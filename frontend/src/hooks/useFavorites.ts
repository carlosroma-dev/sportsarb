import { useCallback, useState } from "react";

const KEY = "mdo.favorites";

function load(): Set<string> {
  try {
    const raw = localStorage.getItem(KEY);
    return new Set<string>(raw ? JSON.parse(raw) : []);
  } catch {
    return new Set<string>();
  }
}

export function useFavorites() {
  const [ids, setIds] = useState<Set<string>>(load);
  const toggle = useCallback((id: string) => {
    setIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      localStorage.setItem(KEY, JSON.stringify([...next]));
      return next;
    });
  }, []);
  const has = useCallback((id: string) => ids.has(id), [ids]);
  return { ids, toggle, has };
}
