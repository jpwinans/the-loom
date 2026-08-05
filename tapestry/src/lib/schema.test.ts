import Ajv from "ajv";
import { readFileSync } from "node:fs";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const schema = JSON.parse(readFileSync("schema/bundle.schema.json", "utf8"));
const fixture = JSON.parse(readFileSync("fixtures/dev-bundle.json", "utf8"));

function validator() {
  return new Ajv({ strict: false });
}

describe("dev fixture conforms to the committed bundle schema", () => {
  it("validates", () => {
    expect(validator().validate(schema, fixture)).toBe(true);
  });

  // Negative controls. A schema this test cannot actually enforce (an
  // unresolved $ref, a permissive `additionalProperties`) would let the
  // positive case above pass vacuously, so pin that malformed bundles fail.
  it("rejects an unknown top-level key", () => {
    const bad = { ...fixture, bogusSection: {} };
    expect(validator().validate(schema, bad)).toBe(false);
  });

  it("rejects a meta field of the wrong type", () => {
    const bad = { ...fixture, meta: { ...fixture.meta, entityCount: "12" } };
    expect(validator().validate(schema, bad)).toBe(false);
  });

  it("rejects a bundle missing a required section", () => {
    const { relations: _relations, ...bad } = fixture;
    expect(validator().validate(schema, bad)).toBe(false);
  });
});

/** Property name -> required?, read off a TS interface or inline type literal. */
function tsMembers(members: ts.NodeArray<ts.TypeElement>): Map<string, boolean> {
  const out = new Map<string, boolean>();
  for (const member of members) {
    if (ts.isPropertySignature(member) && member.name) {
      out.set(member.name.getText(), member.questionToken === undefined);
    }
  }
  return out;
}

function interfaceMembers(source: ts.SourceFile, name: string): ts.NodeArray<ts.TypeElement> {
  for (const statement of source.statements) {
    if (ts.isInterfaceDeclaration(statement) && statement.name.text === name) {
      return statement.members;
    }
  }
  throw new Error(`interface ${name} not found`);
}

function literalMembers(
  members: ts.NodeArray<ts.TypeElement>,
  property: string,
): ts.NodeArray<ts.TypeElement> {
  for (const member of members) {
    if (
      ts.isPropertySignature(member) &&
      member.name?.getText() === property &&
      member.type &&
      ts.isTypeLiteralNode(member.type)
    ) {
      return member.type.members;
    }
  }
  throw new Error(`property ${property} is not an inline object type`);
}

/** Property name -> required?, read off a JSON Schema object node. */
function schemaMembers(node: Record<string, any>): Map<string, boolean> {
  const required: string[] = node.required ?? [];
  return new Map(
    Object.keys(node.properties ?? {}).map((key) => [key, required.includes(key)]),
  );
}

// The bundle crosses the Python/TS seam as an untypecheckable string sentinel:
// nothing links `TapestryBundleRaw` to the Pydantic models, so a field renamed
// on the Python side leaves the TS type silently describing a shape that no
// longer arrives. The committed schema is the shared referent — hold the
// hand-written TS type to it, in both directions.
describe("TapestryBundleRaw matches the committed bundle schema", () => {
  const source = ts.createSourceFile(
    "data.ts",
    readFileSync("src/lib/data.ts", "utf8"),
    ts.ScriptTarget.Latest,
    true,
  );
  const bundle = interfaceMembers(source, "TapestryBundleRaw");

  it("declares the same top-level fields, with the same optionality", () => {
    expect(tsMembers(bundle)).toEqual(schemaMembers(schema));
  });

  it("declares the same meta fields, with the same optionality", () => {
    expect(tsMembers(literalMembers(bundle, "meta"))).toEqual(
      schemaMembers(schema.$defs.TapestryMeta),
    );
  });

  it("declares the same truncated fields, with the same optionality", () => {
    const meta = literalMembers(bundle, "meta");
    expect(tsMembers(literalMembers(meta, "truncated"))).toEqual(
      schemaMembers(schema.$defs.Truncated),
    );
  });

  it("declares the same semantic fields, with the same optionality", () => {
    expect(tsMembers(literalMembers(bundle, "semantic"))).toEqual(
      schemaMembers(schema.$defs.SemanticSection),
    );
  });
});
