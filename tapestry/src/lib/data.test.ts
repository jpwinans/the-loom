import { afterEach, describe, expect, it, vi } from "vitest";
import { BundleLoadError, loadBundle, parseInlineBundle } from "./data";

describe("parseInlineBundle", () => {
  it("returns null for the sentinel (dev mode)", () => {
    expect(parseInlineBundle("__TAPESTRY_BUNDLE__")).toBeNull();
  });
  it("parses injected JSON", () => {
    const bundle = parseInlineBundle(
      JSON.stringify({ schemaVersion: 1, meta: { graph: "g" }, entities: [], relations: [] }),
    );
    expect(bundle?.meta.graph).toBe("g");
  });
});

function setDataBlock(text: string): void {
  document.body.innerHTML = `<script id="tapestry-data" type="application/json">${text}</script>`;
}

const VALID_BUNDLE = { schemaVersion: 1, meta: { graph: "g" }, entities: [], relations: [] };

describe("loadBundle failure paths", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("live API: rejects with a BundleLoadError naming the source on a non-ok response", async () => {
    setDataBlock(JSON.stringify({ live: true, apiBase: "/api" }));
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("boom", { status: 500, statusText: "Internal Server Error" })),
    );
    await expect(loadBundle()).rejects.toMatchObject({
      name: "BundleLoadError",
      source: "the live API",
    });
  });

  it("live API: rejects with a BundleLoadError when the fetch itself throws", async () => {
    setDataBlock(JSON.stringify({ live: true, apiBase: "/api" }));
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(loadBundle()).rejects.toBeInstanceOf(BundleLoadError);
  });

  it("live API: rejects with a BundleLoadError on malformed JSON", async () => {
    setDataBlock(JSON.stringify({ live: true, apiBase: "/api" }));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not json")));
    await expect(loadBundle()).rejects.toMatchObject({
      name: "BundleLoadError",
      source: "the live API",
    });
  });

  it("live API: resolves normally on a valid response", async () => {
    setDataBlock(JSON.stringify({ live: true, apiBase: "/api" }));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(VALID_BUNDLE))));
    await expect(loadBundle()).resolves.toMatchObject({ meta: { graph: "g" } });
  });

  it("dev fixture: rejects with a BundleLoadError naming the source when absent/broken", async () => {
    // No #tapestry-data block at all -> falls through to the dev-fixture fetch.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("not found", { status: 404, statusText: "Not Found" })),
    );
    await expect(loadBundle()).rejects.toMatchObject({
      name: "BundleLoadError",
      source: "the dev fixture",
    });
  });

  it("dev fixture: resolves normally on a valid response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(VALID_BUNDLE))));
    await expect(loadBundle()).resolves.toMatchObject({ meta: { graph: "g" } });
  });

  it("inline bundle: never touches fetch and returns the parsed bundle", async () => {
    setDataBlock(JSON.stringify(VALID_BUNDLE));
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    await expect(loadBundle()).resolves.toMatchObject({ meta: { graph: "g" } });
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
