import { describe, expect, it } from "vitest";
import { parseHash, serializeState } from "./urlHash";

describe("url hash state", () => {
  it("round-trips view, selection, and filters", () => {
    const state = {
      view: "overview" as const,
      selection: "e42",
      filters: { entityTypes: ["claim"], relationTypes: [], confidenceMin: 0.5, statuses: [] },
    };
    expect(parseHash(serializeState(state))).toEqual(state);
  });
  it("round-trips a chronicle time position", () => {
    const state = {
      view: "chronicle" as const,
      selection: null,
      filters: { entityTypes: [], relationTypes: [], confidenceMin: 0, statuses: [] },
      time: 1720000000000,
    };
    expect(parseHash(serializeState(state))).toEqual(state);
  });
  it("returns {} for an empty or malformed hash", () => {
    expect(parseHash("")).toEqual({});
    expect(parseHash("#garbage")).toEqual({});
  });
});
