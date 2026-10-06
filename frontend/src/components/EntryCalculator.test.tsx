import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import EntryCalculator from "./EntryCalculator";
import type { SignalDTO } from "../types";

const mockSignal: SignalDTO = {
  signal_id: "test-1",
  match: "Flamengo x Botafogo",
  metric: "goals",
  metric_label: "Gols",
  scope_label: "Over/Under",
  subject: null,
  period_label: "Tempo normal",
  line: "2.5",
  kickoff: "21:00",
  profit_pct: "2.34",
  over_bookmaker: "KTO",
  over_odd: "2.10",
  over_url: null,
  over_stake: "476.19",
  under_bookmaker: "Betano",
  under_odd: "1.95",
  under_url: null,
  under_stake: "523.81",
  settlement_warning: false,
  highlight: false,
};

describe("EntryCalculator", () => {
  it("renderiza inputs de Total, Stake OVER, Stake UNDER", () => {
    render(<EntryCalculator signal={mockSignal} onClose={vi.fn()} />);
    expect(screen.getByLabelText(/total/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/stake over/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/stake under/i)).toBeInTheDocument();
  });

  it("ao digitar total, recalcula stake1 e stake2", async () => {
    render(<EntryCalculator signal={mockSignal} onClose={vi.fn()} />);
    const totalInput = screen.getByLabelText(/total/i);
    fireEvent.change(totalInput, { target: { value: "1000" } });
    // Os valores de stake devem ser preenchidos (não vazios)
    const stake1 = screen.getByLabelText(/stake over/i) as HTMLInputElement;
    const stake2 = screen.getByLabelText(/stake under/i) as HTMLInputElement;
    expect(parseFloat(stake1.value)).toBeGreaterThan(0);
    expect(parseFloat(stake2.value)).toBeGreaterThan(0);
  });

  it("ao digitar stake1, recalcula stake2 e total", () => {
    render(<EntryCalculator signal={mockSignal} onClose={vi.fn()} />);
    const stake1Input = screen.getByLabelText(/stake over/i);
    fireEvent.change(stake1Input, { target: { value: "500" } });
    const stake2 = screen.getByLabelText(/stake under/i) as HTMLInputElement;
    const total = screen.getByLabelText(/total/i) as HTMLInputElement;
    expect(parseFloat(stake2.value)).toBeGreaterThan(0);
    expect(parseFloat(total.value)).toBeCloseTo(500 + parseFloat(stake2.value), 0);
  });

  it("botão Salvar fica desabilitado quando odds são inválidas", () => {
    const brokenSignal = { ...mockSignal, over_odd: "0.5", under_odd: "0.3" };
    render(<EntryCalculator signal={brokenSignal} onClose={vi.fn()} />);
    const saveBtn = screen.getByRole("button", { name: /salvar/i });
    expect(saveBtn).toBeDisabled();
  });

  it("botão Limpar reseta os campos", () => {
    render(<EntryCalculator signal={mockSignal} onClose={vi.fn()} />);
    const totalInput = screen.getByLabelText(/total/i);
    fireEvent.change(totalInput, { target: { value: "1000" } });
    fireEvent.click(screen.getByRole("button", { name: /limpar/i }));
    expect((totalInput as HTMLInputElement).value).toBe("");
  });
});
