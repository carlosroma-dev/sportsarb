import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const save = vi.fn().mockResolvedValue(undefined);
let useSettingsReturn: {
  settings: { minArb: string; bankroll: string; excludedBookmakers: string[] };
  loading: boolean;
  error: string | null;
  saving: boolean;
  saveError: string | null;
  saved: boolean;
  save: typeof save;
};

vi.mock("../hooks/useSettings", () => ({
  useSettings: () => useSettingsReturn,
}));

vi.mock("../services/signals", () => ({
  fetchBookmakers: vi.fn().mockResolvedValue([
    { id: "betano", label: "Betano" },
    { id: "sportingbet", label: "Sportingbet" },
  ]),
}));

import { fetchBookmakers } from "../services/signals";
import Configuracoes from "./Configuracoes";

function resetSettings(overrides: Partial<typeof useSettingsReturn> = {}) {
  useSettingsReturn = {
    settings: { minArb: "3", bankroll: "1000", excludedBookmakers: [] },
    loading: false,
    error: null,
    saving: false,
    saveError: null,
    saved: false,
    save,
    ...overrides,
  };
}

describe("Configuracoes", () => {
  beforeEach(() => {
    save.mockClear();
    resetSettings();
  });

  it("mostra estado de carregamento", async () => {
    resetSettings({ loading: true });
    render(<Configuracoes />);
    expect(screen.getByText(/carregando preferências/i)).toBeInTheDocument();
    // A busca de casas roda independente do loading das preferências; espera
    // ela assentar para não vazar um "not wrapped in act" pro próximo teste.
    await waitFor(() => expect(vi.mocked(fetchBookmakers)).toHaveBeenCalled());
  });

  it("mostra erro de carregamento", async () => {
    resetSettings({ error: "falha ao conectar" });
    render(<Configuracoes />);
    expect(screen.getByText(/falha ao conectar/i)).toBeInTheDocument();
    await waitFor(() => expect(vi.mocked(fetchBookmakers)).toHaveBeenCalled());
  });

  it("lista as casas disponíveis para veto", async () => {
    render(<Configuracoes />);
    await waitFor(() => expect(screen.getByText("Betano")).toBeInTheDocument());
    expect(screen.getByText("Sportingbet")).toBeInTheDocument();
  });

  it("salva min %, banca e casas vetadas ao clicar em Salvar", async () => {
    render(<Configuracoes />);
    await waitFor(() => expect(screen.getByText("Sportingbet")).toBeInTheDocument());

    await userEvent.clear(screen.getByLabelText(/min % padrão/i));
    await userEvent.type(screen.getByLabelText(/min % padrão/i), "5");
    await userEvent.click(screen.getByLabelText("Sportingbet"));
    await userEvent.click(screen.getByRole("button", { name: /salvar/i }));

    expect(save).toHaveBeenCalledWith(
      expect.objectContaining({ minArb: "5", excludedBookmakers: ["sportingbet"] }),
    );
  });

  it("mostra confirmação de salvo", async () => {
    resetSettings({ saved: true });
    render(<Configuracoes />);
    expect(screen.getByText("Salvo!")).toBeInTheDocument();
    await waitFor(() => expect(vi.mocked(fetchBookmakers)).toHaveBeenCalled());
  });

  it("mostra erro de salvamento", async () => {
    resetSettings({ saveError: "rede indisponível" });
    render(<Configuracoes />);
    expect(screen.getByText(/rede indisponível/i)).toBeInTheDocument();
    await waitFor(() => expect(vi.mocked(fetchBookmakers)).toHaveBeenCalled());
  });
});
