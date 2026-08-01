/**
 * computeOverviewStats — a pure roll-up of a TapestryBundle for the Overview.
 *
 * Everything the dashboard reads is derived here in one pass over the bundle's
 * own `entities` / `relations` arrays (not the graphology model, which drops
 * dangling relations) so integrity signals like `danglingRelationCount` survive.
 * Type and relation tallies mirror the Explorer's FilterPanel facets, but count
 * from the bundle rather than the graph — the two views agree because both read
 * the same entity/relation documents.
 *
 * Confidence binning follows the plan exactly: `Math.min(9, floor(score * 10))`,
 * ten bins over [0, 1]. Entities without a confidence score are left out of the
 * histogram and surfaced separately as `unscoredCount`, so the dashboard can
 * report coverage honestly instead of implying every entity was evaluated.
 */
import type { TapestryBundleRaw } from "../../lib/data";

/** One row of the centrality table: an entity and its PageRank score. */
export interface CentralNode {
  id: string;
  name: string;
  score: number;
}

export interface OverviewStats {
  /** Entity count per entityType, across every bundle entity. */
  typeCounts: Record<string, number>;
  /** Relation count per relationType, across every bundle relation. */
  relationTypeCounts: Record<string, number>;
  /** Ten bins over [0, 1]; only entities carrying a confidence score land here. */
  confidenceHistogram: number[];
  /** Relations whose `from` or `to` is not among the bundle's entity ids. */
  danglingRelationCount: number;
  /** Relations of type `contradicts`. */
  contradictionCount: number;
  /** Up to ten entities with the highest PageRank, descending. */
  topCentral: CentralNode[];
  /** Entities that carry a confidence score (are in the histogram). */
  scoredCount: number;
  /** Entities with no confidence score (excluded from the histogram). */
  unscoredCount: number;
}

/** PageRank map when the analytics section is present; empty otherwise. */
interface AnalyticsShape {
  centrality?: { pagerank?: Record<string, number> };
}

function readScore(doc: Record<string, unknown>): number | null {
  const conf = doc.confidence as { score?: number } | null | undefined;
  return conf && typeof conf.score === "number" ? conf.score : null;
}

export function computeOverviewStats(bundle: TapestryBundleRaw): OverviewStats {
  const typeCounts: Record<string, number> = {};
  const idSet = new Set<string>();
  const nameById = new Map<string, string>();
  const confidenceHistogram = new Array<number>(10).fill(0);
  let scoredCount = 0;
  let unscoredCount = 0;

  for (const entity of bundle.entities) {
    const id = entity.id as string | undefined;
    if (id != null) {
      idSet.add(id);
      nameById.set(id, (entity.name as string) ?? id);
    }
    const type = (entity.entityType as string) ?? "concept";
    typeCounts[type] = (typeCounts[type] ?? 0) + 1;

    const score = readScore(entity);
    if (score === null) {
      unscoredCount += 1;
    } else {
      scoredCount += 1;
      const bin = Math.min(9, Math.floor(score * 10));
      confidenceHistogram[bin] += 1;
    }
  }

  const relationTypeCounts: Record<string, number> = {};
  let danglingRelationCount = 0;
  let contradictionCount = 0;

  for (const relation of bundle.relations) {
    const relType = (relation.relationType as string) ?? "related_to";
    relationTypeCounts[relType] = (relationTypeCounts[relType] ?? 0) + 1;
    if (relType === "contradicts") contradictionCount += 1;
    const from = relation.from as string;
    const to = relation.to as string;
    if (!idSet.has(from) || !idSet.has(to)) danglingRelationCount += 1;
  }

  const analytics = bundle.analytics as AnalyticsShape | undefined;
  const pagerank = analytics?.centrality?.pagerank ?? {};
  const topCentral: CentralNode[] = Object.entries(pagerank)
    .map(([id, score]) => ({ id, name: nameById.get(id) ?? id, score }))
    .sort((a, b) => b.score - a.score)
    .slice(0, 10);

  return {
    typeCounts,
    relationTypeCounts,
    confidenceHistogram,
    danglingRelationCount,
    contradictionCount,
    topCentral,
    scoredCount,
    unscoredCount,
  };
}
