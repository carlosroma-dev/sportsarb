import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../services/preferences", () => ({
  fetchPreferences: vi.fn().mockResolvedValue({ minArb: "2", bankroll: "500", excludedBookmakers: [] }),
  savePreferences: vi.fn().mockResolvedValue(undefined),
}));

import { useSettings } from "./useSettings";

describe("useSettings", () => {
  it("loads local preferences", async () => {
    const { result } = renderHook(() => useSettings());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.settings.bankroll).toBe("500");
  });
});
