import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import SignalCard from "./SignalCard";
import type { SignalDTO } from "../types";

const sig: SignalDTO = {
  signal_id: "x", match: "Alemanha x Paraguai", metric: "throw_ins",
  metric_label: "Laterais", scope_label: "por time", subject: "Paraguai",
  period_label: "Tempo integral", line: "17.5", kickoff: "29/06 17:30",
  profit_pct: "6.44", over_bookmaker: "betano", over_odd: "2.02", over_url: "https://betano",
  over_stake: "316.16", under_bookmaker: "superbet", under_odd: "2.25", under_url: null,
  under_stake: "283.84", settlement_warning: true, highlight: true,
};

describe("SignalCard", () => {
  it("renderiza todos os campos relevantes do sinal", () => {
    render(<SignalCard signal={sig} favorite={false} onToggleFavorite={vi.fn()} />);
    expect(screen.getByText("+6.44%")).toBeInTheDocument();
    expect(screen.getByText("Alemanha x Paraguai")).toBeInTheDocument();
    expect(screen.getByText("Laterais · por time")).toBeInTheDocument();
    expect(screen.getByText("Paraguai")).toBeInTheDocument();
    expect(screen.getByText(/conferir settlement/i)).toBeInTheDocument();
    expect(screen.getByText(/17\.5/)).toBeInTheDocument();
    expect(screen.getByText(/2\.02/)).toBeInTheDocument();
    expect(screen.getByText(/2\.25/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /montar entrada/i })).toBeInTheDocument();
  });
});
