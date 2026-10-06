import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("./services/signals", () => ({
  fetchMarkets: vi.fn().mockResolvedValue([]),
  fetchBookmakers: vi.fn().mockResolvedValue([]),
  fetchSignals: vi.fn().mockResolvedValue({ signals: [], collected_by_house: {}, updated_at: null }),
}));
vi.mock("./services/preferences", () => ({
  fetchPreferences: vi.fn().mockResolvedValue({ minArb: "3", bankroll: "1000", excludedBookmakers: [] }),
  savePreferences: vi.fn(),
}));

import App from "./App";

describe("portfolio routing", () => {
  it("opens the dashboard without a login", async () => {
    window.history.pushState({}, "", "/dashboard");
    render(<App />);
    expect(await screen.findByText(/Demo local/i)).toBeInTheDocument();
  });
});
