import { useState } from "react";
import { useLocation } from "react-router-dom";
import MobileNavDrawer from "./MobileNavDrawer";

const PAGE_TITLES: Record<string, string> = {
  "/dashboard":       "Dashboard",
  "/scanners":        "Scanners",
  "/favoritos":       "Favoritos",
  "/meus-resultados": "Meus Resultados",
  "/historico":       "Histórico",
  "/configuracoes":   "Configurações",
};

export default function Header() {
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const title = PAGE_TITLES[location.pathname] ?? "Mestre das Odds";

  return (
    <>
      <header className="flex items-center justify-between border-b border-white/5 bg-card/50 px-2 py-3 md:px-6 backdrop-blur-sm">
        <div className="flex min-w-0 items-center gap-1">
          <button
            onClick={() => setMenuOpen(true)}
            aria-label="Abrir menu"
            aria-haspopup="dialog"
            aria-expanded={menuOpen}
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-muted transition hover:bg-surface/60 hover:text-white md:hidden"
          >
            <span aria-hidden className="text-xl leading-none">☰</span>
          </button>
          <h1 className="truncate px-2 text-base font-semibold md:px-0">{title}</h1>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            onClick={() => window.dispatchEvent(new CustomEvent("signals:refresh"))}
            className="flex h-11 w-11 items-center justify-center rounded-lg border border-white/10 text-sm text-muted transition hover:border-primary/40 hover:text-white sm:h-auto sm:w-auto sm:px-2.5 sm:py-1.5"
            aria-label="Atualizar sinais"
            title="Atualizar"
          >
            ↻
          </button>
          <span className="rounded-lg border border-primary/30 bg-primary/10 px-2.5 py-1.5 text-xs font-semibold text-primary">
            Demo local · dados fictícios
          </span>
        </div>
      </header>
      <MobileNavDrawer open={menuOpen} onClose={() => setMenuOpen(false)} />
    </>
  );
}
