export interface CalcResult {
  total: number;
  stake1: number;
  stake2: number;
  retorno: number;
  lucro: number;
  roi: number;
}

export function isValidOdd(odd: number): boolean {
  return Number.isFinite(odd) && odd > 1;
}

export function calcFromTotal(total: number, odd1: number, odd2: number): CalcResult {
  const sum = 1 / odd1 + 1 / odd2;
  const retorno = total / sum;
  const stake1 = retorno / odd1;
  const stake2 = retorno / odd2;
  const lucro = retorno - total;
  const roi = (lucro / total) * 100;
  return { total, stake1, stake2, retorno, lucro, roi };
}

export function calcFromStake1(stake1: number, odd1: number, odd2: number): CalcResult {
  const retorno = stake1 * odd1;
  const stake2 = retorno / odd2;
  const total = stake1 + stake2;
  const lucro = retorno - total;
  const roi = (lucro / total) * 100;
  return { total, stake1, stake2, retorno, lucro, roi };
}

export function calcFromStake2(stake2: number, odd1: number, odd2: number): CalcResult {
  const retorno = stake2 * odd2;
  const stake1 = retorno / odd1;
  const total = stake1 + stake2;
  const lucro = retorno - total;
  const roi = (lucro / total) * 100;
  return { total, stake1, stake2, retorno, lucro, roi };
}

const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const pct = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function formatBRL(n: number): string {
  return brl.format(n);
}

export function formatPct(n: number): string {
  return pct.format(n) + "%";
}
