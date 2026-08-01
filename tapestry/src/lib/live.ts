/**
 * live.ts — live-mode detection and the live REST client.
 *
 * The server serves the committed template with its `__TAPESTRY_BUNDLE__`
 * sentinel replaced (server-side, per request, via the same render_html the
 * static path uses) by `{"live": true, "apiBase": "/api"}`. We detect live mode
 * by that PARSED SHAPE (`live === true`) — never by the sentinel literal, so the
 * literal stays out of app source and can't be constant-folded. Static mode
 * (a real inline bundle) and dev mode (the unparseable sentinel) both return
 * null here and fall through to loadBundle's existing branches.
 */
export interface LiveConfig {
  live: true;
  apiBase: string;
}

export function detectLive(): LiveConfig | null {
  const block = document.getElementById("tapestry-data");
  if (!block) return null;
  try {
    const parsed = JSON.parse(block.textContent ?? "") as { live?: unknown; apiBase?: unknown };
    if (parsed && parsed.live === true) {
      return { live: true, apiBase: typeof parsed.apiBase === "string" ? parsed.apiBase : "/api" };
    }
  } catch {
    // dev sentinel — not JSON
  }
  return null;
}

export async function fetchGraphs(apiBase: string): Promise<string[]> {
  const response = await fetch(`${apiBase}/graphs`);
  const graphs = (await response.json()) as { name: string }[];
  return graphs.map((g) => g.name);
}
