import { describe, expect, it } from "vitest";
import { parseInlineBundle } from "./data";

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
