/**
 * The 19 EntityType values in theloom/model.py enum order. Each type's colour is
 * the CSS custom property `--type-<value>` defined in `tokens.css` (validated in
 * both themes). `typeColorVar()` returns the token reference; later views (legend,
 * node fills, Overview bars) should read colour through it, never hard-code hex.
 */
export const ENTITY_TYPES = [
  "concept",
  "claim",
  "source",
  "question",
  "evidence",
  "pattern",
  "insight",
  "tension",
  "convergence",
  "system",
  "variable",
  "loop",
  "leverage_point",
  "event",
  "procedure",
  "hypothesis",
  "inference_rule",
  "inference_trace",
  "research_session",
] as const;

export type EntityTypeName = (typeof ENTITY_TYPES)[number];

/**
 * CSS value referencing an entity type's categorical token. Unknown or legacy
 * types fall back to the muted ink token so a colour is always defined.
 */
export function typeColorVar(type: string): string {
  return (ENTITY_TYPES as readonly string[]).includes(type)
    ? `var(--type-${type})`
    : "var(--color-text-3)";
}
