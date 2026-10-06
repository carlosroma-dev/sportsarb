import { describe, it, expect } from "vitest";
import {
  calcFromTotal,
  calcFromStake1,
  calcFromStake2,
  isValidOdd,
  formatBRL,
  formatPct,
} from "./arbCalc";

describe("calcFromTotal", () => {
  it("distribui stakes proporcionalmente às probabilidades implícitas", () => {
    const r = calcFromTotal(1000, 2.0, 2.0);
    expect(r.stake1).toBeCloseTo(500, 1);
    expect(r.stake2).toBeCloseTo(500, 1);
    expect(r.total).toBeCloseTo(1000, 1);
    expect(r.retorno).toBeCloseTo(1000, 1);
  });

  it("calcula lucro positivo quando há arbitragem real (soma < 1)", () => {
    const r = calcFromTotal(1000, 2.10, 2.05);
    expect(r.lucro).toBeGreaterThan(0);
    expect(r.roi).toBeGreaterThan(0);
  });

  it("retorno = stake1*odd1 e retorno = stake2*odd2 (consistência)", () => {
    const r = calcFromTotal(600, 2.10, 1.95);
    expect(r.stake1 * 2.10).toBeCloseTo(r.retorno, 2);
    expect(r.stake2 * 1.95).toBeCloseTo(r.retorno, 2);
  });
});

describe("calcFromStake1", () => {
  it("recalcula stake2 e total corretamente", () => {
    const r = calcFromStake1(476.19, 2.10, 1.95);
    expect(r.retorno).toBeCloseTo(476.19 * 2.10, 1);
    expect(r.stake2).toBeCloseTo(r.retorno / 1.95, 1);
    expect(r.total).toBeCloseTo(r.stake1 + r.stake2, 1);
  });

  it("lucro = retorno - total", () => {
    const r = calcFromStake1(500, 2.10, 2.05);
    expect(r.lucro).toBeCloseTo(r.retorno - r.total, 4);
  });
});

describe("calcFromStake2", () => {
  it("recalcula stake1 e total corretamente", () => {
    const r = calcFromStake2(513, 1.95, 2.10);
    expect(r.retorno).toBeCloseTo(513 * 2.10, 1);
    expect(r.stake1).toBeCloseTo(r.retorno / 1.95, 1);
    expect(r.total).toBeCloseTo(r.stake1 + r.stake2, 1);
  });
});

describe("isValidOdd", () => {
  it("aceita odds acima de 1", () => {
    expect(isValidOdd(1.5)).toBe(true);
    expect(isValidOdd(50)).toBe(true);
  });

  it("rejeita odd <= 1, NaN e Infinity", () => {
    expect(isValidOdd(1)).toBe(false);
    expect(isValidOdd(0)).toBe(false);
    expect(isValidOdd(NaN)).toBe(false);
    expect(isValidOdd(Infinity)).toBe(false);
  });
});

describe("formatBRL", () => {
  it("formata número como moeda brasileira", () => {
    expect(formatBRL(1234.56)).toMatch(/1\.234,56/);
  });
});

describe("formatPct", () => {
  it("formata percentual com 2 casas", () => {
    expect(formatPct(2.345)).toBe("2,35%");
  });
});
