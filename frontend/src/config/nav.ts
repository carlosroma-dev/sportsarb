export interface NavItem {
  to: string;
  label: string;
  icon: string;
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/dashboard", label: "Dashboard", icon: "⊞" },
  { to: "/meus-resultados", label: "Meus Resultados", icon: "◈" },
  { to: "/configuracoes", label: "Configurações", icon: "⊙" },
];
