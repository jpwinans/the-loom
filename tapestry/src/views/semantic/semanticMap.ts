/**
 * semanticMap — pure helpers for the embedding scatter view.
 *
 * The projection is the layout: `buildSemanticGraph` places each entity at its
 * `semantic.projection` coordinate, so no ForceAtlas2 runs. Hull geometry
 * (convex hull per cluster) and the lasso hit-test (point-in-polygon) are
 * space-agnostic — the view feeds them VIEWPORT positions so hulls and lassoes
 * track the Sigma camera. All unit-tested without WebGL.
 */
import Graph from "graphology";
import type { TapestryBundleRaw } from "../../lib/data";
import { resolveTypeColor } from "../explorer/buildGraph";

export interface Point {
  x: number;
  y: number;
}

export interface ScatterPoint extends Point {
  id: string;
  entityType: string;
  label: string;
}

interface Cluster {
  id: number;
  label: string;
  entityIds: string[];
  size: number;
}

export function buildScatter(bundle: TapestryBundleRaw): ScatterPoint[] {
  const projection = bundle.semantic?.projection ?? {};
  const byId = new Map(bundle.entities.map((e) => [e.id as string, e]));
  const points: ScatterPoint[] = [];
  for (const [id, coord] of Object.entries(projection)) {
    const entity = byId.get(id);
    if (!entity || coord.length < 2) continue;
    points.push({
      id,
      x: coord[0],
      y: coord[1],
      entityType: (entity.entityType as string) ?? "concept",
      label: (entity.name as string) ?? id,
    });
  }
  return points;
}

export function buildSemanticGraph(bundle: TapestryBundleRaw): Graph {
  const graph = new Graph({ multi: true, type: "directed" });
  for (const point of buildScatter(bundle)) {
    if (graph.hasNode(point.id)) continue;
    graph.addNode(point.id, {
      label: point.label,
      entityType: point.entityType,
      x: point.x,
      y: point.y,
      size: 6,
      color: resolveTypeColor(point.entityType),
    });
  }
  return graph;
}

function cross(o: Point, a: Point, b: Point): number {
  return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
}

export function convexHull(points: Point[]): Point[] {
  const unique = Array.from(
    new Map(points.map((p) => [`${p.x},${p.y}`, p])).values(),
  ).sort((p, q) => (p.x === q.x ? p.y - q.y : p.x - q.x));
  if (unique.length < 3) return unique;

  const half = (src: Point[]): Point[] => {
    const out: Point[] = [];
    for (const p of src) {
      while (out.length >= 2 && cross(out[out.length - 2], out[out.length - 1], p) <= 0) {
        out.pop();
      }
      out.push(p);
    }
    out.pop();
    return out;
  };
  const lower = half(unique);
  const upper = half([...unique].reverse());
  return [...lower, ...upper];
}

export function pointInPolygon(p: Point, polygon: Point[]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i, i += 1) {
    const a = polygon[i];
    const b = polygon[j];
    const straddles = a.y > p.y !== b.y > p.y;
    if (straddles && p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x) {
      inside = !inside;
    }
  }
  return inside;
}

export function pointsInLasso(
  polygon: Point[],
  points: (Point & { id: string })[],
): string[] {
  if (polygon.length < 3) return [];
  return points.filter((point) => pointInPolygon(point, polygon)).map((point) => point.id);
}

export function clusterPolygons(
  clusters: Cluster[],
  positionById: Map<string, Point>,
): { id: number; label: string; hull: Point[] }[] {
  const polygons: { id: number; label: string; hull: Point[] }[] = [];
  for (const cluster of clusters) {
    const positioned = cluster.entityIds
      .map((id) => positionById.get(id))
      .filter((p): p is Point => p != null);
    if (positioned.length < 2) continue;
    polygons.push({ id: cluster.id, label: cluster.label, hull: convexHull(positioned) });
  }
  return polygons;
}
