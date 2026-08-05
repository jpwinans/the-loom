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

/** The coarse JSON type a field carries, the one thing both sides can express. */
type Kind = "string" | "number" | "boolean" | "array" | "object" | "unknown";

/** `"required number"` / `"optional object"` — the comparable per-field fact. */
type Facet = string;

function facet(required: boolean, kind: Kind): Facet {
  return `${required ? "required" : "optional"} ${kind}`;
}

function tsKind(node: ts.TypeNode | undefined): Kind {
  if (!node) return "unknown";
  if (ts.isArrayTypeNode(node)) return "array";
  if (ts.isTypeLiteralNode(node)) return "object";
  switch (node.kind) {
    case ts.SyntaxKind.StringKeyword:
      return "string";
    case ts.SyntaxKind.NumberKeyword:
      return "number";
    case ts.SyntaxKind.BooleanKeyword:
      return "boolean";
  }
  if (ts.isTypeReferenceNode(node)) {
    const name = node.typeName.getText();
    if (name === "Array" || name === "ReadonlyArray") return "array";
    if (name === "Record" || name === "Map") return "object";
  }
  return "unknown";
}

/**
 * Property name -> required?/type, read off a TS interface or inline type
 * literal. Comparing names and optionality alone would let a Python-side type
 * change (`entityCount: int` becoming a string) slip through with every name
 * still matching, so the JSON type travels with each field.
 */
function tsMembers(members: ts.NodeArray<ts.TypeElement>): Map<string, Facet> {
  const out = new Map<string, Facet>();
  for (const member of members) {
    if (ts.isPropertySignature(member) && member.name) {
      out.set(member.name.getText(), facet(member.questionToken === undefined, tsKind(member.type)));
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

/** The element type of an inline `{ ... }[]` property, e.g. `temporal.events`. */
function arrayItemMembers(
  members: ts.NodeArray<ts.TypeElement>,
  property: string,
): ts.NodeArray<ts.TypeElement> {
  for (const member of members) {
    if (
      ts.isPropertySignature(member) &&
      member.name?.getText() === property &&
      member.type &&
      ts.isArrayTypeNode(member.type) &&
      ts.isTypeLiteralNode(member.type.elementType)
    ) {
      return member.type.elementType.members;
    }
  }
  throw new Error(`property ${property} is not an inline array-of-object type`);
}

/** Unwrap Pydantic's `anyOf: [T, null]` optional encoding down to the T node. */
function unwrapNullable(node: Record<string, any>): Record<string, any> {
  if (!Array.isArray(node.anyOf)) return node;
  const options = node.anyOf.filter((option: Record<string, any>) => option.type !== "null");
  return options.length === 1 ? options[0] : node;
}

function schemaKind(node: Record<string, any>): Kind {
  const inner = unwrapNullable(node);
  if (inner.$ref) return "object";
  switch (inner.type) {
    case "string":
      return "string";
    case "integer":
    case "number":
      return "number";
    case "boolean":
      return "boolean";
    case "array":
      return "array";
    case "object":
      return "object";
    default:
      return "unknown";
  }
}

/** Property name -> required?/type, read off a JSON Schema object node. */
function schemaMembers(node: Record<string, any>): Map<string, Facet> {
  const required: string[] = node.required ?? [];
  return new Map(
    Object.entries<Record<string, any>>(node.properties ?? {}).map(([key, value]) => [
      key,
      facet(required.includes(key), schemaKind(value)),
    ]),
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

  it("declares the same semantic cluster fields, with the same optionality", () => {
    const semantic = literalMembers(bundle, "semantic");
    expect(tsMembers(arrayItemMembers(semantic, "clusters"))).toEqual(
      schemaMembers(schema.$defs.SemanticCluster),
    );
  });

  it("declares the same analytics fields, with the same optionality", () => {
    expect(tsMembers(literalMembers(bundle, "analytics"))).toEqual(
      schemaMembers(schema.$defs.AnalyticsSection),
    );
  });

  it("declares the same temporal fields, with the same optionality", () => {
    expect(tsMembers(literalMembers(bundle, "temporal"))).toEqual(
      schemaMembers(schema.$defs.TemporalSection),
    );
  });

  it("declares the same temporal event fields, with the same optionality", () => {
    const temporal = literalMembers(bundle, "temporal");
    expect(tsMembers(arrayItemMembers(temporal, "events"))).toEqual(
      schemaMembers(schema.$defs.TemporalEvent),
    );
  });

  // Negative controls on the comparison itself: matching names and matching
  // optionality are not enough, so pin that a type-only drift is caught too.
  it("notices a field whose type changed but whose name and optionality did not", () => {
    const drifted = ts.createSourceFile(
      "drift.ts",
      "interface D { entityCount: string; sections: string[] }",
      ts.ScriptTarget.Latest,
      true,
    );
    const members = tsMembers(interfaceMembers(drifted, "D"));
    expect(members.get("entityCount")).toBe("required string");
    expect(members.get("entityCount")).not.toEqual(
      schemaMembers(schema.$defs.TapestryMeta).get("entityCount"),
    );
    expect(members.get("sections")).toEqual(
      schemaMembers(schema.$defs.TapestryMeta).get("sections"),
    );
  });

  it("notices a list-valued field that became an object", () => {
    const drifted = ts.createSourceFile(
      "drift.ts",
      "interface D { components: Record<string, unknown> }",
      ts.ScriptTarget.Latest,
      true,
    );
    expect(tsMembers(interfaceMembers(drifted, "D")).get("components")).not.toEqual(
      schemaMembers(schema.$defs.AnalyticsSection).get("components"),
    );
  });
});
