import { afterEach, describe, expect, it, vi } from "vitest";
import { detectLive } from "./live";

function setDataBlock(text: string): void {
  document.body.innerHTML = `<script id="tapestry-data" type="application/json">${text}</script>`;
}

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("detectLive", () => {
  it("returns the config when the marker says live", () => {
    setDataBlock(JSON.stringify({ live: true, apiBase: "/api" }));
    expect(detectLive()).toEqual({ live: true, apiBase: "/api" });
  });

  it("defaults apiBase to /api when omitted", () => {
    setDataBlock(JSON.stringify({ live: true }));
    expect(detectLive()).toEqual({ live: true, apiBase: "/api" });
  });

  it("returns null for a static inline bundle", () => {
    setDataBlock(JSON.stringify({ schemaVersion: 1, meta: {}, entities: [], relations: [] }));
    expect(detectLive()).toBeNull();
  });

  it("returns null for the dev sentinel (unparseable)", () => {
    setDataBlock("__TAPESTRY_BUNDLE__");
    expect(detectLive()).toBeNull();
  });
});
