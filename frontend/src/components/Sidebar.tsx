import { NavLink } from "react-router-dom";
import { NAV_ITEMS } from "../config/nav";
import Logo from "./Logo";

export default function Sidebar() {

  return (
    <aside className="hidden w-56 shrink-0 flex-col border-r border-white/5 bg-card md:flex">
      <div className="flex items-center justify-center border-b border-white/5 px-4 py-5">
        <Logo className="h-[136px]" />
      </div>
      <nav className="flex flex-1 flex-col gap-0.5 p-2">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition ${
                isActive
                  ? "bg-surface text-primary font-medium"
                  : "text-muted hover:bg-surface/60 hover:text-white"
              }`
            }
          >
            <span aria-hidden className="text-base">{item.icon}</span>
            {item.label}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-white/5 p-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/15 text-xs font-bold text-primary">
            D
          </div>
          <div className="min-w-0">
            <div className="truncate text-xs font-medium">Portf?lio</div>
            <div className="text-[11px] text-muted">Modo local</div>
          </div>
        </div>
      </div>
    </aside>
  );
}
