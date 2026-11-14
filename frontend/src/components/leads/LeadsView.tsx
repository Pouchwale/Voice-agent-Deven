"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Download,
  FunnelX,
  Headphones,
  LoaderCircle,
  Megaphone,
  Pencil,
  Phone,
  Trash2,
  Upload,
  UserPlus,
  Users,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { CreateCampaignModal } from "@/components/campaigns/CreateCampaignModal";
import { OutboundOffNotice } from "@/components/campaigns/OutboundOffNotice";
import { useCallNow } from "@/components/calls/useCallNow";
import { useTestAgent } from "@/components/providers/TestAgentProvider";
import { Button, ButtonLink, IconButton } from "@/components/ui/Button";
import { INTEREST_ICON, InterestBadge } from "@/components/ui/InterestBadge";
import { SearchInput } from "@/components/ui/Input";
import { ConfirmDialog } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { Pagination } from "@/components/ui/Pagination";
import { RatingPill } from "@/components/ui/RatingPill";
import { Segmented, Select } from "@/components/ui/Select";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/States";
import { LeadStatusChip } from "@/components/ui/StatusChip";
import { Checkbox } from "@/components/ui/Switch";
import { useToast } from "@/components/ui/Toast";
import { useDebounced } from "@/hooks/useDebounced";
import { useEntrance } from "@/hooks/useEntrance";
import { useNow } from "@/hooks/useNow";
import { api, errorMessage, keys } from "@/lib/api";
import { INTEREST_ACTIVE } from "@/lib/theme";
import { cn } from "@/lib/cn";
import { LEADS_PAGE_SIZE } from "@/lib/config";
import {
  formatDate,
  formatNumber,
  formatPhone,
  formatRelative,
  INTEREST_ALL,
  INTEREST_LABEL,
  LEAD_STATUSES,
  LEAD_STATUS_LABEL,
  leadName,
} from "@/lib/format";
import type { Interest, Lead, LeadSort, LeadStatus, Page } from "@/lib/types";
import { ImportCsvModal } from "./ImportCsvModal";
import { LeadFormModal } from "./LeadFormModal";

const SORTS: LeadSort[] = ["created_at", "-created_at", "rating", "-rating", "last_called_at", "-last_called_at", "name", "-name"];

type SortField = "name" | "rating" | "last_called_at" | "created_at";

function SortHeader({
  field,
  label,
  sort,
  onSort,
  className,
}: {
  field: SortField;
  label: string;
  sort: LeadSort;
  onSort: (s: LeadSort) => void;
  className?: string;
}) {
  const active = sort === field || sort === `-${field}`;
  const desc = sort === `-${field}`;
  const next: LeadSort = active ? ((desc ? field : `-${field}`) as LeadSort) : ((field === "name" ? field : `-${field}`) as LeadSort);
  return (
    <th className={className} aria-sort={active ? (desc ? "descending" : "ascending") : "none"}>
      <button
        type="button"
        onClick={() => onSort(next)}
        className={cn("-mx-1.5 inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 hover:bg-canvas", active && "bg-subtle")}
      >
        {label}
        {active ? desc ? <ArrowDown className="size-3.5" aria-hidden /> : <ArrowUp className="size-3.5" aria-hidden /> : <ArrowUpDown className="size-3.5 opacity-60" aria-hidden />}
      </button>
    </th>
  );
}

export function LeadsView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const toast = useToast();
  const { mutate: globalMutate } = useSWRConfig();
  const testAgent = useTestAgent();
  const { callNow, callingId, outboundOff, outboundHint } = useCallNow();
  const now = useNow(60_000);

  // URL is the source of truth for filters, so links like /leads?interest=hot work.
  const status = (LEAD_STATUSES as string[]).includes(params.get("status") ?? "") ? (params.get("status") as LeadStatus) : "";
  const interest = (INTEREST_ALL as string[]).includes(params.get("interest") ?? "") ? (params.get("interest") as Interest) : "";
  const sort: LeadSort = (SORTS as string[]).includes(params.get("sort") ?? "") ? (params.get("sort") as LeadSort) : "-created_at";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const urlSearch = params.get("search") ?? "";

  const [q, setQ] = useState(urlSearch);
  const debouncedQ = useDebounced(q.trim(), 300);

  const setParams = useCallback(
    (patch: Record<string, string | number | null>, resetPage = true) => {
      const next = new URLSearchParams(params.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === "" || v === undefined) next.delete(k);
        else next.set(k, String(v));
      }
      if (resetPage && !("page" in patch)) next.delete("page");
      const s = next.toString();
      router.replace(s ? `${pathname}?${s}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  useEffect(() => {
    if (debouncedQ !== urlSearch) setParams({ search: debouncedQ || null });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only react to the debounced input
  }, [debouncedQ]);

  const listKey = keys.leads({ search: urlSearch, status, interest, sort, page, page_size: LEADS_PAGE_SIZE });
  const { data, error, isLoading, isValidating, mutate } = useSWR<Page<Lead>>(listKey, { keepPreviousData: true });

  // Deleting the last rows of the last page (or a stale ?page= link) leaves us past the end:
  // jump to the last real page instead of showing the "Import your first leads" empty state.
  const lastPage = data ? Math.max(1, Math.ceil(data.total / (data.page_size || LEADS_PAGE_SIZE))) : 1;
  const pastEnd = !!data && !isValidating && data.items.length === 0 && data.total > 0 && page > lastPage;
  useEffect(() => {
    if (pastEnd) setParams({ page: lastPage > 1 ? lastPage : null }, false);
  }, [pastEnd, lastPage, setParams]);

  // Modals
  const [showAdd, setShowAdd] = useState(false);
  const [showImport, setShowImport] = useState(params.get("import") === "1");
  const [editing, setEditing] = useState<Lead | null>(null);
  const [deleting, setDeleting] = useState<Lead | null>(null);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [campaignOpen, setCampaignOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  // Selection survives paging; cleared after bulk actions.
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const pageIds = useMemo(() => data?.items.map((l) => l.id) ?? [], [data]);
  const allOnPage = pageIds.length > 0 && pageIds.every((id) => selected.has(id));
  const someOnPage = pageIds.some((id) => selected.has(id));

  const rootRef = useEntrance<HTMLDivElement>(!!data);

  const hasFilters = !!(urlSearch || status || interest);

  function toggle(id: number, on: boolean) {
    setSelected((s) => {
      const n = new Set(s);
      if (on) n.add(id);
      else n.delete(id);
      return n;
    });
  }

  function toggleAll(on: boolean) {
    setSelected((s) => {
      const n = new Set(s);
      pageIds.forEach((id) => (on ? n.add(id) : n.delete(id)));
      return n;
    });
  }

  function refreshAll() {
    void globalMutate((key) => typeof key === "string" && (key.startsWith("/api/leads") || key.startsWith("/api/dashboard")));
  }

  async function confirmDelete() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.leads.remove(deleting.id);
      toast({ tone: "success", title: "Lead deleted", description: leadName(deleting) });
      toggle(deleting.id, false);
      refreshAll();
      setDeleting(null);
    } catch (err) {
      toast({ tone: "error", title: "Couldn't delete lead", description: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  async function confirmBulkDelete() {
    setBusy(true);
    try {
      const res = await api.leads.bulkDelete([...selected]);
      toast({ tone: "success", title: `Deleted ${formatNumber(res.deleted)} leads` });
      setSelected(new Set());
      refreshAll();
      setBulkDeleting(false);
    } catch (err) {
      toast({ tone: "error", title: "Bulk delete failed", description: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  const interestOptions = [
    { value: "" as const, label: "All" },
    ...(["cold", "warm", "hot", "unknown"] as Interest[]).map((i) => {
      const Icon = INTEREST_ICON[i];
      return { value: i, label: INTEREST_LABEL[i], icon: <Icon aria-hidden />, activeClass: INTEREST_ACTIVE[i] };
    }),
  ];

  return (
    <div ref={rootRef}>
      <PageHeader
        title="Leads"
        description={data ? `${formatNumber(data.total)} ${hasFilters ? "matching" : "total"} leads` : "Everyone the agent can call."}
        actions={
          <>
            <ButtonLink href={api.leads.exportUrl({ interest: "hot" })} download icon={<Download />}>
              Export hot leads
            </ButtonLink>
            <Button variant="secondary" icon={<Upload />} onClick={() => setShowImport(true)}>
              Import CSV
            </Button>
            <Button variant="primary" icon={<UserPlus />} onClick={() => setShowAdd(true)}>
              Add lead
            </Button>
          </>
        }
      />

      {/* Filters: one row above the table */}
      <div className="mb-4 flex flex-wrap items-center gap-3" data-anim>
        <SearchInput value={q} onChange={setQ} placeholder="Search name, phone, company, city" label="Search leads" className="w-full sm:w-72" />
        <Select<LeadStatus | "">
          aria-label="Filter by status"
          pill
          className="w-44"
          value={status}
          onChange={(v) => setParams({ status: v || null })}
          options={[{ value: "", label: "All statuses" }, ...LEAD_STATUSES.map((s) => ({ value: s, label: LEAD_STATUS_LABEL[s] }))]}
        />
        <Segmented<Interest | "">
          label="Filter by interest"
          size="sm"
          value={interest}
          onChange={(v) => setParams({ interest: v || null })}
          options={interestOptions}
        />
        {hasFilters ? (
          <Button
            size="sm"
            variant="ghost"
            icon={<FunnelX />}
            onClick={() => {
              setQ("");
              setParams({ search: null, status: null, interest: null });
            }}
          >
            Clear filters
          </Button>
        ) : null}
      </div>

      {outboundOff ? (
        <OutboundOffNotice className="mb-4">
          Outbound calling is off in Settings, so “Call now” is disabled. Browser tests still work.
        </OutboundOffNotice>
      ) : null}

      {/* Bulk action bar */}
      {selected.size > 0 ? (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-full border border-line bg-primary-tint py-2 pr-2 pl-4" role="region" aria-label="Bulk actions">
          <p className="text-sm font-semibold">{formatNumber(selected.size)} selected</p>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <Button size="sm" variant="primary" icon={<Megaphone />} onClick={() => setCampaignOpen(true)}>
              Create campaign from selected
            </Button>
            <Button size="sm" variant="danger" icon={<Trash2 />} onClick={() => setBulkDeleting(true)}>
              Delete
            </Button>
            <IconButton size="sm" label="Clear selection" icon={<X />} onClick={() => setSelected(new Set())} />
          </div>
        </div>
      ) : null}

      <section className="overflow-hidden rounded-xl border border-line bg-white" data-anim>
        {error && !data ? (
          <div className="p-4">
            <ErrorState error={error} onRetry={() => void mutate()} title="Couldn't load leads" />
          </div>
        ) : !data || (data.items.length === 0 && data.total > 0) ? (
          <SkeletonRows rows={8} cols={6} />
        ) : data.items.length === 0 ? (
          hasFilters ? (
            <EmptyState
              icon={<FunnelX />}
              title="No leads match"
              body="Try a different search or clear the filters."
              action={
                <Button
                  icon={<FunnelX />}
                  onClick={() => {
                    setQ("");
                    setParams({ search: null, status: null, interest: null });
                  }}
                >
                  Clear filters
                </Button>
              }
            />
          ) : (
            <EmptyState
              icon={<Users />}
              tone="subtle"
              title="Import your first leads"
              body="Upload a CSV with names and phone numbers. The agent calls them, qualifies them and scores each lead."
              action={
                <>
                  <Button variant="secondary" icon={<Upload />} onClick={() => setShowImport(true)}>
                    Import CSV
                  </Button>
                  <Button icon={<UserPlus />} onClick={() => setShowAdd(true)}>
                    Add one lead
                  </Button>
                </>
              }
            />
          )
        ) : (
          <>
            {error ? (
              <div className="border-b border-line p-3">
                <ErrorState compact error={error} onRetry={() => void mutate()} title="Showing the last loaded page" />
              </div>
            ) : null}
            <div className={cn("relative overflow-x-auto transition-opacity", isValidating && !isLoading && "opacity-70")}>
              <table className="data-table min-w-[860px]">
                <thead>
                  <tr>
                    <th className="w-10">
                      <Checkbox checked={allOnPage} indeterminate={someOnPage} onChange={toggleAll} label="Select all leads on this page" />
                    </th>
                    <SortHeader field="name" label="Lead" sort={sort} onSort={(s) => setParams({ sort: s })} />
                    <th>Phone</th>
                    <th className="hidden 2xl:table-cell">City</th>
                    <th>Status</th>
                    <th>Interest</th>
                    <SortHeader field="rating" label="Rating" sort={sort} onSort={(s) => setParams({ sort: s })} />
                    <SortHeader field="last_called_at" label="Last called" sort={sort} onSort={(s) => setParams({ sort: s })} />
                    <SortHeader
                      field="created_at"
                      label="Added"
                      sort={sort}
                      onSort={(s) => setParams({ sort: s })}
                      className="hidden lg:table-cell"
                    />
                    <th className="text-right">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((l) => (
                    <tr
                      key={l.id}
                      className="row-link"
                      data-selected={selected.has(l.id) || undefined}
                      onClick={() => router.push(`/leads/${l.id}`)}
                    >
                      <td onClick={(e) => e.stopPropagation()}>
                        <Checkbox checked={selected.has(l.id)} onChange={(on) => toggle(l.id, on)} label={`Select ${leadName(l)}`} />
                      </td>
                      <td className="max-w-[240px]">
                        <Link href={`/leads/${l.id}`} className="block truncate font-semibold hover:underline" onClick={(e) => e.stopPropagation()}>
                          {leadName(l)}
                        </Link>
                        {l.company ? <span className="block truncate text-xs font-normal text-muted">{l.company}</span> : null}
                      </td>
                      <td className="tabular whitespace-nowrap font-normal">{formatPhone(l.phone)}</td>
                      <td className="hidden max-w-[140px] truncate font-normal 2xl:table-cell">{l.city ?? "—"}</td>
                      <td>
                        <LeadStatusChip status={l.status} />
                      </td>
                      <td>
                        <InterestBadge interest={l.interest} size="xs" />
                      </td>
                      <td>
                        <RatingPill rating={l.rating} />
                      </td>
                      <td className="whitespace-nowrap font-normal" title={l.last_called_at ?? undefined}>
                        {l.last_called_at ? formatRelative(l.last_called_at, now) : <span className="text-muted">Never</span>}
                        {l.attempts > 0 ? <span className="block text-xs text-muted">{l.attempts} attempts</span> : null}
                      </td>
                      <td className="hidden whitespace-nowrap font-normal lg:table-cell">{formatDate(l.created_at)}</td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-end gap-1.5">
                          <IconButton
                            size="sm"
                            variant="primary"
                            label={
                              l.status === "dnc"
                                ? `${leadName(l)} is marked do-not-call`
                                : outboundHint
                                  ? `Can't call ${leadName(l)}: ${outboundHint}`
                                  : `Call ${leadName(l)} now`
                            }
                            icon={callingId === l.id ? <LoaderCircle className="spin" /> : <Phone />}
                            disabled={outboundOff || l.status === "dnc" || l.status === "calling" || callingId === l.id}
                            onClick={() => void callNow({ id: l.id, name: leadName(l) })}
                          />
                          <IconButton size="sm" label={`Test ${leadName(l)} in browser`} icon={<Headphones />} onClick={() => testAgent.open({ lead: l })} />
                          <IconButton size="sm" label={`Edit ${leadName(l)}`} icon={<Pencil />} onClick={() => setEditing(l)} />
                          <IconButton size="sm" variant="danger" label={`Delete ${leadName(l)}`} icon={<Trash2 />} onClick={() => setDeleting(l)} />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              page={data.page || page}
              pageSize={data.page_size || LEADS_PAGE_SIZE}
              total={data.total}
              onPage={(p) => setParams({ page: p > 1 ? p : null }, false)}
              busy={isValidating && !isLoading}
            />
          </>
        )}
      </section>

      <LeadFormModal open={showAdd} onClose={() => setShowAdd(false)} />
      <LeadFormModal open={!!editing} lead={editing} onClose={() => setEditing(null)} />
      <ImportCsvModal
        open={showImport}
        onClose={() => {
          setShowImport(false);
          if (params.get("import")) setParams({ import: null }, false);
        }}
      />
      <CreateCampaignModal
        open={campaignOpen}
        onClose={() => setCampaignOpen(false)}
        selectedIds={[...selected]}
        initialFilter={{ status: status || undefined, interest: interest || undefined, search: urlSearch || undefined }}
        onCreated={() => setSelected(new Set())}
      />
      <ConfirmDialog
        open={!!deleting}
        onClose={() => setDeleting(null)}
        onConfirm={() => void confirmDelete()}
        title="Delete lead?"
        body={
          <>
            <strong>{leadName(deleting)}</strong> and their call history will be removed. This can&apos;t be undone.
          </>
        }
        confirmLabel="Delete"
        danger
        busy={busy}
      />
      <ConfirmDialog
        open={bulkDeleting}
        onClose={() => setBulkDeleting(false)}
        onConfirm={() => void confirmBulkDelete()}
        title={`Delete ${formatNumber(selected.size)} leads?`}
        body="The selected leads and their call history will be removed. This can't be undone."
        confirmLabel="Delete all"
        danger
        busy={busy}
      />
    </div>
  );
}
