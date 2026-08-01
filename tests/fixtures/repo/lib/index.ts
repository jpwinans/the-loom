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
