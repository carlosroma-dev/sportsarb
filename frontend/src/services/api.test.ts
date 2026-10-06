import MockAdapter from "axios-mock-adapter";
import { describe, expect, it } from "vitest";
import { api } from "./api";

describe("local API client", () => {
  it("loads without a credential header", async () => {
    const mock = new MockAdapter(api);
    mock.onGet("/api/v1/signals").reply((config) => {
      expect(config.headers?.Authorization).toBeUndefined();
      return [200, { signals: [] }];
    });
    await api.get("/api/v1/signals");
    mock.restore();
  });
});
