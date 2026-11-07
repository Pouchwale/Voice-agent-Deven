"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { useLiveStore } from "@/components/providers/LiveProvider";
import { useToast } from "@/components/ui/Toast";
import { api, errorMessage, keys } from "@/lib/api";
import { leadName, STAGE_LABEL, STAGES } from "@/lib/format";
import type { BoardColumn, BoardResponse, Lead, LeadSort, Stage } from "@/lib/types";

export const BOARD_PAGE_SIZE = 50;

/** Sort that matches the board's own column order, so "Load more" pages line up. */
const COLUMN_SORT: Record<Stage, LeadSort> = {
  new: "-created_at",
  no_answer: "-last_called_at",
  cold: "-rating",
  warm: "-rating",
  hot: "-rating",
};

type PerStage<T> = Record<Stage, T>;
const emptyPerStage = <T,>(v: () => T): PerStage<T> => ({ new: v(), no_answer: v(), cold: v(), warm: v(), hot: v() });

/** Optimistic move: where the card is going, and the column it was in when the first drag started. */
interface PendingMove {
  from: Stage;
  to: Stage;
}

const updatedMs = (l: Lead) => {
  const t = Date.parse(l.updated_at);
  return Number.isFinite(t) ? t : 0;
};

export interface ViewColumn {
  key: Stage;
  title: string;
  count: number;
  leads: Lead[];
  canLoadMore: boolean;
  loadingMore: boolean;
}

/**
 * Board data: first `limit` leads per column from /api/leads/board, extra pages per column from
 * /api/leads?stage=, optimistic drag-and-drop moves (PATCH {stage}) and a throttled live refresh.
 */
export function useBoard(search: string) {
  const toast = useToast();
  const store = useLiveStore();
  const { data, error, isLoading, isValidating, mutate } = useSWR<BoardResponse>(keys.board(search, BOARD_PAGE_SIZE), {
    keepPreviousData: true,
  });

  // Leads loaded with "Load more" (per column), the next page to fetch, and columns with no more pages.
  const [extra, setExtra] = useState<PerStage<Lead[]>>(() => emptyPerStage(() => []));
  const [nextPage, setNextPage] = useState<PerStage<number>>(() => emptyPerStage(() => 2));
  const [exhausted, setExhausted] = useState<PerStage<boolean>>(() => emptyPerStage(() => false));
  const [loadingMore, setLoadingMore] = useState<PerStage<boolean>>(() => emptyPerStage(() => false));
  // Optimistic moves: lead id -> move (cleared when the server confirms or rejects).
  const [pending, setPending] = useState<Map<number, PendingMove>>(() => new Map());
  const pendingRef = useRef(pending);
  // Bumped whenever the extra pages are thrown away, so a "Load more" still in flight can't append stale rows.
  const generation = useRef(0);
  const inFlight = useRef(new Set<Stage>());
  // Per-lead move bookkeeping: latest move token, and a promise chain so PATCHes for one lead go out in order.
  const moveSeq = useRef(new Map<number, number>());
  const moveChain = useRef(new Map<number, Promise<unknown>>());

  const updatePending = useCallback((fn: (m: Map<number, PendingMove>) => Map<number, PendingMove>) => {
    pendingRef.current = fn(pendingRef.current);
    setPending(pendingRef.current);
  }, []);

  const resetExtra = useCallback(() => {
    generation.current += 1;
    inFlight.current.clear();
    setExtra(emptyPerStage(() => []));
    setNextPage(emptyPerStage(() => 2));
    setExhausted(emptyPerStage(() => false));
    setLoadingMore(emptyPerStage(() => false));
  }, []);

  // A new search resets the extra pages.
  useEffect(() => {
    resetExtra();
  }, [search, resetExtra]);

  // Refresh when the live feed reports call changes - at most once every 2 s.
  const lastRefresh = useRef(0);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    const off = store.onCallUpdate(() => {
      const wait = Math.max(0, 2000 - (Date.now() - lastRefresh.current));
      if (refreshTimer.current) return;
      refreshTimer.current = setTimeout(() => {
        refreshTimer.current = null;
        lastRefresh.current = Date.now();
        void mutate();
      }, wait);
    });
    return () => {
      off();
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      refreshTimer.current = null;
    };
  }, [store, mutate]);

  const columns: ViewColumn[] = useMemo(() => {
    const byKey = new Map<Stage, BoardColumn>((data?.columns ?? []).map((c) => [c.key, c]));
    const counts = emptyPerStage(() => 0);
    for (const key of STAGES) counts[key] = byKey.get(key)?.count ?? 0;

    // One copy per lead. The board response wins; a "Load more" row or a PATCH result only replaces it
    // when it is strictly newer - otherwise a stale extra copy would pin the card to its old column
    // after the server (or the next AI-scored call) moved it.
    const best = new Map<number, { lead: Lead; key: Stage }>();
    const baseKey = new Map<number, Stage>();
    for (const key of STAGES) {
      for (const lead of byKey.get(key)?.leads ?? []) {
        if (best.has(lead.id)) continue;
        best.set(lead.id, { lead, key });
        baseKey.set(lead.id, key);
      }
    }
    for (const key of STAGES) {
      for (const lead of extra[key]) {
        const cur = best.get(lead.id);
        if (!cur || updatedMs(lead) > updatedMs(cur.lead)) best.set(lead.id, { lead, key });
      }
    }

    const lists = emptyPerStage<Lead[]>(() => []);
    const movedIn = emptyPerStage<Lead[]>(() => []);
    for (const [id, { lead, key }] of best) {
      const move = pending.get(id);
      if (move && move.to !== key) {
        movedIn[move.to].push({ ...lead, stage: move.to });
      } else {
        lists[key].push(lead);
      }
      // Counts come from the server; shift them while the server data still has the card elsewhere.
      if (move) {
        const origin = baseKey.get(id) ?? move.from;
        if (origin !== move.to) {
          counts[origin] = Math.max(0, counts[origin] - 1);
          counts[move.to] += 1;
        }
      }
    }

    return STAGES.map((key) => {
      const leads = [...movedIn[key], ...lists[key]];
      return {
        key,
        title: byKey.get(key)?.title || STAGE_LABEL[key],
        count: counts[key],
        leads,
        canLoadMore: !exhausted[key] && leads.length < counts[key],
        loadingMore: loadingMore[key],
      };
    });
  }, [data, extra, pending, loadingMore, exhausted]);

  const columnOf = useRef(new Map<number, Stage>());
  useEffect(() => {
    const m = new Map<number, Stage>();
    for (const c of columns) for (const l of c.leads) m.set(l.id, c.key);
    columnOf.current = m;
  }, [columns]);

  const loadMore = useCallback(
    async (key: Stage) => {
      if (inFlight.current.has(key)) return;
      inFlight.current.add(key);
      const gen = generation.current;
      setLoadingMore((s) => ({ ...s, [key]: true }));
      try {
        const page = nextPage[key];
        const res = await api.leads.list({ stage: key, search, page, page_size: BOARD_PAGE_SIZE, sort: COLUMN_SORT[key] });
        if (gen !== generation.current) return; // search changed / board refreshed meanwhile
        const items = Array.isArray(res.items) ? res.items : [];
        setExtra((s) => ({ ...s, [key]: [...s[key], ...items] }));
        setNextPage((s) => ({ ...s, [key]: page + 1 }));
        // A short page means the column is done (also stops an endless button when rows shifted columns).
        if (items.length < BOARD_PAGE_SIZE) setExhausted((s) => ({ ...s, [key]: true }));
      } catch (err) {
        if (gen === generation.current) toast({ tone: "error", title: "Couldn't load more leads", description: errorMessage(err) });
      } finally {
        if (gen === generation.current) {
          inFlight.current.delete(key);
          setLoadingMore((s) => ({ ...s, [key]: false }));
        }
      }
    },
    [nextPage, search, toast],
  );

  const moveLead = useCallback(
    async (lead: Lead, to: Stage) => {
      const id = lead.id;
      const current = pendingRef.current.get(id);
      const shownIn = current?.to ?? columnOf.current.get(id) ?? lead.stage;
      if (shownIn === to) return;
      const seq = (moveSeq.current.get(id) ?? 0) + 1;
      moveSeq.current.set(id, seq);
      const isLatest = () => moveSeq.current.get(id) === seq;
      updatePending((m) => new Map(m).set(id, { from: current?.from ?? shownIn, to }));

      // Two quick drags of the same card: send the PATCHes in order so the server ends on the last one.
      const prev = moveChain.current.get(id) ?? Promise.resolve();
      const req = prev.catch(() => undefined).then(() => api.leads.update(id, { stage: to }));
      moveChain.current.set(id, req);
      const name = leadName(lead);
      try {
        const updated = await req;
        if (!isLatest()) return; // a newer move of this card owns the UI now
        // Keep the updated lead visible in its new column even if it isn't in the first page there.
        setExtra((s) => {
          const next = emptyPerStage<Lead[]>(() => []);
          for (const k of STAGES) next[k] = s[k].filter((l) => l.id !== id);
          next[to] = [updated, ...next[to]];
          return next;
        });
        await mutate();
        if (isLatest()) toast({ tone: "success", title: `Moved ${name} to ${STAGE_LABEL[to]}` });
      } catch (err) {
        if (isLatest()) toast({ tone: "error", title: `Couldn't move ${name}`, description: errorMessage(err) });
      } finally {
        if (isLatest()) {
          updatePending((m) => {
            const n = new Map(m);
            n.delete(id);
            return n;
          });
          if (moveChain.current.get(id) === req) moveChain.current.delete(id);
        }
      }
    },
    [mutate, toast, updatePending],
  );

  const refresh = useCallback(() => {
    // An explicit refresh starts over from the server's first page per column.
    resetExtra();
    return mutate();
  }, [mutate, resetExtra]);

  return { columns, data, error, isLoading, isValidating, refresh, loadMore, moveLead };
}
