import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../services/api", () => ({
  api: { get: vi.fn().mockResolvedValue({ data: [] }), post: vi.fn(), patch: vi.fn() },
}));

import { api } from "../services/api";
import { useOperations } from "./useOperations";

describe("useOperations", () => {
  it("loads operations from the local API", async () => {
    const { result } = renderHook(() => useOperations());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(api.get).toHaveBeenCalledWith("/api/v1/operations");
  });
});
