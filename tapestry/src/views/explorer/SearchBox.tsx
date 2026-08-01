/**
 * SearchBox — fuzzy find any entity by name or type and fly the camera to it.
 *
 * A fuse.js index over `{id, name, entityType}` (rebuilt only when the graph
 * changes) backs a combobox: typing filters, ↑/↓ walk the results, Enter or a
 * click hands the chosen id to `onNavigate`, which selects it and animates the
 * camera. The input carries a stable id so a later global "/" shortcut can focus
 * it (Task 13). Chrome only — no graph marks are recoloured here.
 */
import Fuse from "fuse.js";
import { useMemo, useRef, useState } from "react";
import { useGraph } from "../../lib/BundleContext";
import { typeColorVar } from "../../design/palette";

interface SearchItem {
  id: string;
  name: string;
  entityType: string;
}

const MAX_RESULTS = 8;

export function SearchBox({ onNavigate }: { onNavigate: (id: string) => void }) {
  const graph = useGraph();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  const items = useMemo<SearchItem[]>(() => {
    const out: SearchItem[] = [];
    graph.forEachNode((id, attr) => {
      out.push({
        id,
        name: (attr.label as string) ?? id,
        entityType: (attr.entityType as string) ?? "concept",
      });
    });
    return out;
  }, [graph]);

  const fuse = useMemo(
    () => new Fuse(items, { keys: ["name", "entityType"], threshold: 0.4, ignoreLocation: true }),
    [items],
  );

  const results = useMemo<SearchItem[]>(() => {
    const q = query.trim();
    if (!q) return [];
    return fuse.search(q, { limit: MAX_RESULTS }).map((r) => r.item);
  }, [query, fuse]);

  const showList = open && results.length > 0;

  const choose = (item: SearchItem | undefined) => {
    if (!item) return;
    onNavigate(item.id);
    setQuery("");
    setOpen(false);
    setActive(0);
    inputRef.current?.blur();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!showList) {
      if (e.key === "Escape") inputRef.current?.blur();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (i + 1) % results.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i - 1 + results.length) % results.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(results[active]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    }
  };

  return (
    <div className="search" role="combobox" aria-expanded={showList} aria-haspopup="listbox">
      <div className="search__field">
        <svg className="search__icon" width="15" height="15" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="7" cy="7" r="4.75" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M10.6 10.6 14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
        <input
          id="explorer-search-input"
          ref={inputRef}
          className="search__input"
          type="text"
          placeholder="Find an entity…"
          value={query}
          aria-label="Find an entity"
          aria-autocomplete="list"
          // Only reference the results listbox while it exists in the DOM —
          // a dangling aria-controls id fails axe's aria-valid-attr-value.
          aria-controls={showList ? "search-results" : undefined}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
            setActive(0);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => window.setTimeout(() => setOpen(false), 120)}
          onKeyDown={onKeyDown}
        />
        {query && (
          <button
            type="button"
            className="search__clear"
            aria-label="Clear search"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              setQuery("");
              setActive(0);
              inputRef.current?.focus();
            }}
          >
            <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true">
              <path
                d="M4 4l8 8M12 4l-8 8"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            </svg>
          </button>
        )}
      </div>

      {showList && (
        <ul id="search-results" className="search__results" role="listbox">
          {results.map((item, i) => (
            <li key={item.id} role="option" aria-selected={i === active}>
              <button
                type="button"
                className={`search__result${i === active ? " search__result--active" : ""}`}
                onMouseDown={(e) => e.preventDefault()}
                onMouseEnter={() => setActive(i)}
                onClick={() => choose(item)}
              >
                <span
                  className="search__swatch"
                  style={{ background: typeColorVar(item.entityType) }}
                  aria-hidden="true"
                />
                <span className="search__name">{item.name}</span>
                <span className="search__type">{item.entityType.replace(/_/g, " ")}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
