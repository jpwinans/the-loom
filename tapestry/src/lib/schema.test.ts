import Ajv from "ajv";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("dev fixture conforms to the committed bundle schema", () => {
  it("validates", () => {
    const schema = JSON.parse(readFileSync("schema/bundle.schema.json", "utf8"));
    const fixture = JSON.parse(readFileSync("fixtures/dev-bundle.json", "utf8"));
    const ajv = new Ajv({ strict: false });
    expect(ajv.validate(schema, fixture)).toBe(true);
  });
});
