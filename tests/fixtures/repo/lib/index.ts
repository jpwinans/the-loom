import { formatBalance } from "./helper";

export interface Ledger {
  entries: number[];
}

export class Reporter {
  constructor(private ledger: Ledger) {}

  summarize(): string {
    const total = this.ledger.entries.reduce((a, b) => a + b, 0);
    return formatBalance(total);
  }
}

export function makeReporter(ledger: Ledger): Reporter {
  return new Reporter(ledger);
}

// A deliberate twin of the helper's roundCents: a name defined twice names no
// single symbol, so a doc mentioning it must not resolve to either.
export function roundCents(amount: number): number {
  return Math.round(amount * 100) / 100;
}
