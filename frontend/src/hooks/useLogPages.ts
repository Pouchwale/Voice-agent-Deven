"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import useSWR, { type SWRConfiguration } from "swr";

export interface LogPage<T> {
  items: T[];
  has_more: boolean;
  total: number;
}

/** Everything loaded so far, and whether the server has entries older than those (null: not asked yet). */
interface Loaded<T> {
  items: T[];
  hasMore: boolean | null;
}

/** Newest first, one copy per id; `fresh` wins over `old` for the same id. */
function merge<T extends { id: number }>(fresh: T[], old: T[]): T[] {
  const byId = new Map<number, T>();
  for (const entry of old) byId.set(entry.id, entry);
  for (const entry of fresh) byId.set(entry.id, entry);
  return Array.from(byId.values()).sort((a, b) => b.id - a.id);
}

/**
 * The fresh first page joined with what was already loaded. Normally the two overlap, and entries a refresh pushed
 * off the first page stay listed. When they do not overlap (more than a whole page of new entries arrived since the
 * last refresh), the entries in between were never fetched: the list then starts again from the fresh page, so
 * nothing is ever skipped silently. "Show older" continues from there.
 */
function join<T extends { id: number }>(page: LogPage<T>, loaded: Loaded<T>): Loaded<T> {
  if (loaded.items.length && page.has_more) {
    const known = new Set(loaded.items.map((entry) => entry.id));
    if (!page.items.some((entry) => known.has(entry.id))) return { items: page.items, hasMore: null };
  }
  return { items: merge(page.items, loaded.items), hasMore: loaded.hasMore };
}

/**
 * A newest-first log that is never trimmed. The first page comes from SWR (so it refreshes like every other list);
 * older pages are loaded on demand and kept.
 *
 * A different `firstKey` is a different log: remount the component (key={firstKey}) to start it clean.
 */
export function useLogPages<T extends { id: number }>(
  firstKey: string,
  fetchOlder: (beforeId: number) => Promise<LogPage<T>>,
  options?: SWRConfiguration<LogPage<T>>,
) {
  const { data, error, mutate } = useSWR<LogPage<T>>(firstKey, options);
  const [loaded, setLoaded] = useState<Loaded<T>>({ items: [], hasMore: null });
  const [loadingOlder, setLoadingOlder] = useState(false);

  useEffect(() => {
    if (data) setLoaded((cur) => join(data, cur));
  }, [data]);

  // Also joined while rendering, so the list is right in the same render that receives a fresh first page.
  const view = useMemo(() => (data ? join(data, loaded) : loaded), [data, loaded]);
  const items = view.items;
  const hasMore = view.hasMore ?? data?.has_more ?? false;
  const oldestId = items.length ? items[items.length - 1].id : null;

  /** Loads the next older page. Rejects when the request fails (the caller shows why). */
  const loadOlder = useCallback(async () => {
    if (oldestId === null || loadingOlder) return;
    setLoadingOlder(true);
    try {
      const page = await fetchOlder(oldestId);
      // Only if the list still ends where this page begins (it may have started again from a fresh first page).
      setLoaded((cur) =>
        cur.items.length && cur.items[cur.items.length - 1].id === oldestId
          ? { items: merge(page.items, cur.items), hasMore: page.has_more }
          : cur,
      );
    } finally {
      setLoadingOlder(false);
    }
  }, [oldestId, loadingOlder, fetchOlder]);

  /** Updates one entry already listed (for a change this page made itself, e.g. marking an entry undone). */
  const patch = useCallback(
    (id: number, fields: Partial<T>) => {
      const apply = (entry: T) => (entry.id === id ? { ...entry, ...fields } : entry);
      setLoaded((cur) => ({ ...cur, items: cur.items.map(apply) }));
      // The first page keeps its own copy (it wins for recent entries): update that one too.
      void mutate((cur) => (cur ? { ...cur, items: cur.items.map(apply) } : cur), { revalidate: false });
    },
    [mutate],
  );

  return {
    items,
    /** How many entries exist in all (not only the loaded ones). */
    total: Math.max(data?.total ?? 0, items.length),
    hasMore,
    loaded: data !== undefined,
    error,
    loadingOlder,
    loadOlder,
    patch,
    mutate,
  };
}
