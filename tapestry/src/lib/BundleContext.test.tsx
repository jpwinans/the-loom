/**
 * BundleProvider failure-path tests: a load failure after a bundle is up (a
 * failed graph switch) must surface a banner over the still-valid data and
 * keep `currentGraph` truthful about what is displayed — never a silent
 * mismatch between the header and the rendered graph.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BundleProvider, useBundle, useLiveControls } from "./BundleContext";

vi.mock("./data", () => ({
  loadBundle: vi.fn(),
}));
vi.mock("./live", () => ({
  detectLive: vi.fn(() => ({ apiBase: "/api" })),
  fetchGraphs: vi.fn(() => Promise.resolve(["A", "B"])),
}));
vi.mock("../views/explorer/buildGraph", () => ({
  buildGraph: vi.fn(() => ({})),
}));

import { loadBundle } from "./data";

const bundleFor = (graph: string) =>
  ({ schemaVersion: 1, meta: { graph }, entities: [], relations: [] }) as never;

function Probe() {
  const { currentGraph, setGraph } = useLiveControls();
  const bundle = useBundle();
  return (
    <div>
      <span data-testid="current">{currentGraph}</span>
      <span data-testid="data">{bundle.meta.graph}</span>
      <button data-testid="switch" onClick={() => setGraph("B")} />
    </div>
  );
}

declare global {
  // eslint-disable-next-line no-var — react's act() requires this flag.
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

const flush = () => act(async () => {});

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe("BundleProvider after a failed graph switch", () => {
  it("shows a banner, keeps the data, and reports the loaded graph", async () => {
    vi.mocked(loadBundle)
      .mockResolvedValueOnce(bundleFor("A"))
      .mockRejectedValueOnce(new Error("api exploded"));

    await act(async () => {
      root.render(
        <BundleProvider>
          <Probe />
        </BundleProvider>,
      );
    });
    expect(container.querySelector('[data-testid="current"]')?.textContent).toBe("A");
    expect(container.querySelector('[role="alert"]')).toBeNull();

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="switch"]')?.click();
    });
    await flush();

    // The old data is still rendered, the header does not advance to the
    // failed graph, and the failure is visible with a retry.
    expect(container.querySelector('[data-testid="data"]')?.textContent).toBe("A");
    expect(container.querySelector('[data-testid="current"]')?.textContent).toBe("A");
    const banner = container.querySelector('[role="alert"]');
    expect(banner?.textContent).toContain("api exploded");
    expect(banner?.querySelector("button")).not.toBeNull();
  });

  it("retry from the banner recovers and advances the header", async () => {
    vi.mocked(loadBundle)
      .mockResolvedValueOnce(bundleFor("A"))
      .mockRejectedValueOnce(new Error("api exploded"))
      .mockResolvedValueOnce(bundleFor("B"));

    await act(async () => {
      root.render(
        <BundleProvider>
          <Probe />
        </BundleProvider>,
      );
    });
    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="switch"]')?.click();
    });
    await flush();
    expect(container.querySelector('[role="alert"]')).not.toBeNull();

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[role="alert"] button')?.click();
    });
    await flush();

    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(container.querySelector('[data-testid="data"]')?.textContent).toBe("B");
    expect(container.querySelector('[data-testid="current"]')?.textContent).toBe("B");
  });

  it("initial-load failure still shows the full-screen error with retry", async () => {
    vi.mocked(loadBundle).mockRejectedValueOnce(new Error("down"));

    await act(async () => {
      root.render(
        <BundleProvider>
          <Probe />
        </BundleProvider>,
      );
    });
    await flush();

    const alert = container.querySelector('[role="alert"]');
    expect(alert?.className).toContain("app__loading--error");
    expect(alert?.textContent).toContain("down");
  });
});
