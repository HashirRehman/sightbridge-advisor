"use client";

import {
  AlertCircle,
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Lock,
  Search,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { AppHeader } from "@/components/app-header";
import { useSession } from "@/components/session-context";
import { REASON_LABELS, StatusBadge } from "@/components/status-badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api, ApiError, type AdminExchange, type AuditPage } from "@/lib/api";

import { ActivityView } from "../chat/activity-view";

import { KnowledgeBasePanel } from "./knowledge-base-panel";

const PAGE_SIZE = 25;
const timestamp = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function useDebounced<T>(value: T, ms = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

export function AuditView() {
  const { me, loading: sessionLoading } = useSession();
  const [tab, setTab] = useState("history");
  const [status, setStatus] = useState("all");
  const [order, setOrder] = useState("newest");
  const [search, setSearch] = useState("");
  const [user, setUser] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<AuditPage | null>(null);
  const [error, setError] = useState<{
    status: number;
    message: string;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<AdminExchange | null>(null);
  const debouncedSearch = useDebounced(search);
  const debouncedUser = useDebounced(user);

  const params = useMemo(() => {
    const p = new URLSearchParams({
      limit: String(PAGE_SIZE),
      offset: String(offset),
      order,
    });
    if (status !== "all") p.set("status", status);
    if (debouncedSearch.trim()) p.set("q", debouncedSearch.trim());
    if (debouncedUser.trim()) p.set("user", debouncedUser.trim());
    return p;
  }, [status, order, offset, debouncedSearch, debouncedUser]);

  useEffect(
    () => setOffset(0),
    [status, order, debouncedSearch, debouncedUser],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .auditLog(params)
      .then((p) => !cancelled && (setPage(p), setError(null)))
      .catch(
        (e: unknown) =>
          !cancelled &&
          setError(
            e instanceof ApiError
              ? { status: e.status, message: e.message }
              : { status: 0, message: "Couldn't load the audit log." },
          ),
      )
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [params]);

  const forbidden =
    error?.status === 403 || (!sessionLoading && me && !me.is_admin);

  return (
    <div className="flex min-h-dvh flex-col">
      <AppHeader />
      <main className="mx-auto w-full max-w-7xl flex-1 space-y-6 px-4 py-8 sm:px-6">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold">Admin dashboard</h1>
          <p className="text-muted-foreground text-sm">
            Review questions and answers, ingested sources, and embedding
            coverage.
          </p>
        </div>

        {forbidden ? (
          <div className="bg-card flex flex-col items-center gap-3 rounded-xl border px-6 py-16 text-center">
            <Lock className="text-muted-foreground size-6" aria-hidden />
            <h2 className="font-semibold">Admin access required</h2>
            <p className="text-muted-foreground max-w-sm text-sm">
              The dashboard is only available to administrators.
            </p>
          </div>
        ) : (
          <>
            <div
              role="tablist"
              aria-label="Dashboard sections"
              className="flex gap-2 border-b pb-3"
            >
              {[
                ["history", "Question history"],
                ["knowledge", "Knowledge base"],
              ].map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  role="tab"
                  id={`tab-${value}`}
                  aria-selected={tab === value}
                  aria-controls={`panel-${value}`}
                  onClick={() => setTab(value)}
                  onKeyDown={(event) => {
                    if (
                      !["ArrowLeft", "ArrowRight", "Home", "End"].includes(
                        event.key,
                      )
                    )
                      return;
                    event.preventDefault();
                    const next =
                      event.key === "Home"
                        ? "history"
                        : event.key === "End"
                          ? "knowledge"
                          : value === "history"
                            ? "knowledge"
                            : "history";
                    setTab(next);
                    document.getElementById(`tab-${next}`)?.focus();
                  }}
                  tabIndex={tab === value ? 0 : -1}
                  className={`cursor-pointer rounded-md px-3 py-2 text-sm font-medium ${tab === value ? "bg-secondary" : "text-muted-foreground hover:bg-muted"}`}
                >
                  {label}
                </button>
              ))}
            </div>
            {tab === "knowledge" ? (
              <div
                role="tabpanel"
                id="panel-knowledge"
                aria-labelledby="tab-knowledge"
              >
                <KnowledgeBasePanel />
              </div>
            ) : (
              <div
                role="tabpanel"
                id="panel-history"
                aria-labelledby="tab-history"
                className="space-y-6"
              >
                <Summary page={page} />

                <section
                  aria-label="Filters"
                  className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_11rem_10rem]"
                >
                  <div className="space-y-1.5">
                    <Label htmlFor="q">Question contains</Label>
                    <div className="relative">
                      <Search
                        className="text-muted-foreground absolute top-1/2 left-3 size-4 -translate-y-1/2"
                        aria-hidden
                      />
                      <Input
                        id="q"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="bg-card pl-9"
                        placeholder="e.g. DST"
                        maxLength={200}
                      />
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="user">User email</Label>
                    <Input
                      id="user"
                      value={user}
                      onChange={(e) => setUser(e.target.value)}
                      className="bg-card"
                      placeholder="e.g. advisor@"
                      maxLength={200}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="status">Outcome</Label>
                    <Select value={status} onValueChange={setStatus}>
                      <SelectTrigger id="status" className="bg-card w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All outcomes</SelectItem>
                        <SelectItem value="answered">Answered</SelectItem>
                        <SelectItem value="refused">Refused</SelectItem>
                        <SelectItem value="unsupported">Unsupported</SelectItem>
                        <SelectItem value="failed">Failed</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="order">Sort</Label>
                    <Select value={order} onValueChange={setOrder}>
                      <SelectTrigger id="order" className="bg-card w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="newest">Newest first</SelectItem>
                        <SelectItem value="oldest">Oldest first</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </section>

                {error && (
                  <Alert variant="destructive" role="alert">
                    <AlertCircle aria-hidden />
                    <AlertDescription>{error.message}</AlertDescription>
                  </Alert>
                )}

                <div className="bg-card overflow-hidden rounded-xl border">
                  <div className="overflow-x-auto">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-44">Time</TableHead>
                          <TableHead className="hidden md:table-cell">
                            User
                          </TableHead>
                          <TableHead>Question</TableHead>
                          <TableHead className="w-32">Outcome</TableHead>
                          <TableHead className="hidden w-20 text-right sm:table-cell">
                            Sources
                          </TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {loading && !page ? (
                          [0, 1, 2, 3].map((i) => (
                            <TableRow key={i}>
                              <TableCell colSpan={5}>
                                <Skeleton className="h-5 w-full" />
                              </TableCell>
                            </TableRow>
                          ))
                        ) : page && page.items.length === 0 ? (
                          <TableRow>
                            <TableCell
                              colSpan={5}
                              className="text-muted-foreground py-12 text-center"
                            >
                              No exchanges match these filters.
                            </TableCell>
                          </TableRow>
                        ) : (
                          page?.items.map((item) => (
                            <TableRow key={item.id} className="align-top">
                              <TableCell className="text-muted-foreground text-xs whitespace-nowrap">
                                {timestamp.format(new Date(item.created_at))}
                              </TableCell>
                              <TableCell className="hidden max-w-[14rem] truncate text-sm md:table-cell">
                                {item.user_email}
                              </TableCell>
                              <TableCell className="max-w-[28rem] whitespace-normal">
                                <button
                                  type="button"
                                  onClick={() => setSelected(item)}
                                  title="View exchange details"
                                  aria-haspopup="dialog"
                                  className="group inline-flex cursor-pointer items-start gap-2 rounded-sm text-left text-sm font-medium underline decoration-border underline-offset-4 hover:decoration-foreground"
                                >
                                  <span className="line-clamp-2">
                                    {item.question}
                                  </span>
                                  <ArrowUpRight
                                    className="text-muted-foreground mt-0.5 size-4 shrink-0 group-hover:text-foreground"
                                    aria-hidden
                                  />
                                </button>
                              </TableCell>
                              <TableCell>
                                <StatusBadge status={item.status} />
                              </TableCell>
                              <TableCell className="hidden text-right text-sm tabular-nums sm:table-cell">
                                {item.citations.length}
                              </TableCell>
                            </TableRow>
                          ))
                        )}
                      </TableBody>
                    </Table>
                  </div>
                  {page && page.total > 0 && (
                    <div className="flex items-center justify-between gap-3 border-t px-4 py-3 text-sm">
                      <span className="text-muted-foreground">
                        {page.offset + 1}–
                        {Math.min(page.offset + page.items.length, page.total)}{" "}
                        of {page.total}
                      </span>
                      <div className="flex gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={offset === 0 || loading}
                          onClick={() =>
                            setOffset(Math.max(0, offset - PAGE_SIZE))
                          }
                        >
                          <ChevronLeft aria-hidden /> Previous
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={offset + PAGE_SIZE >= page.total || loading}
                          onClick={() => setOffset(offset + PAGE_SIZE)}
                        >
                          Next <ChevronRight aria-hidden />
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </main>
      <ExchangeDetail exchange={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

function Summary({ page }: { page: AuditPage | null }) {
  const tiles = [
    { label: "Total exchanges", value: page?.summary.total },
    { label: "Answered", value: page?.summary.answered },
    { label: "Refused", value: page?.summary.refused },
    { label: "Unsupported", value: page?.summary.unsupported },
    { label: "Failed", value: page?.summary.failed },
  ];
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {tiles.map((t) => (
        <div key={t.label} className="bg-card rounded-xl border p-4">
          <dt className="text-muted-foreground text-xs font-medium">
            {t.label}
          </dt>
          <dd className="mt-1 text-2xl font-semibold tabular-nums">
            {t.value ?? <Skeleton className="h-7 w-10" />}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function ExchangeDetail({
  exchange,
  onClose,
}: {
  exchange: AdminExchange | null;
  onClose: () => void;
}) {
  return (
    <Sheet open={exchange !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        {exchange && (
          <>
            <SheetHeader>
              <SheetTitle className="pr-6 leading-snug">
                {exchange.question}
              </SheetTitle>
              <SheetDescription>
                {exchange.user_email} ·{" "}
                {timestamp.format(new Date(exchange.created_at))}
              </SheetDescription>
            </SheetHeader>
            <div className="space-y-6 px-4 pb-8">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={exchange.status} />
                {exchange.reason && (
                  <span className="text-muted-foreground text-xs">
                    {REASON_LABELS[exchange.reason] ?? exchange.reason}
                  </span>
                )}
              </div>
              <section className="space-y-2">
                <h3 className="text-sm font-semibold">
                  Answer shown to the user
                </h3>
                <pre className="bg-muted/60 rounded-lg border p-3 font-sans text-sm leading-relaxed whitespace-pre-wrap">
                  {exchange.answer_text}
                </pre>
              </section>
              {exchange.citations.length > 0 && (
                <section className="space-y-2">
                  <h3 className="text-sm font-semibold">Cited sources</h3>
                  <ol className="space-y-2">
                    {exchange.citations.map((c) => (
                      <li
                        key={c.number}
                        className="rounded-lg border p-3 text-sm"
                      >
                        <a
                          href={c.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-start gap-1.5 font-medium underline-offset-4 hover:underline"
                        >
                          [{c.number}] {c.title}
                          <ExternalLink
                            className="mt-0.5 size-3.5 shrink-0"
                            aria-hidden
                          />
                        </a>
                        <p className="text-muted-foreground text-xs">
                          {c.publisher}
                          {c.location && <> · {c.location}</>} · {c.document_id}
                        </p>
                        {c.quotes.map((q) => (
                          <p
                            key={q}
                            className="text-foreground/80 mt-1.5 text-xs italic"
                          >
                            “{q}”
                          </p>
                        ))}
                      </li>
                    ))}
                  </ol>
                </section>
              )}
              <ActivityView steps={exchange.activity ?? []} />
              <ModelUsageDetails calls={exchange.model_usage ?? []} />
              <section className="space-y-2">
                <h3 className="text-sm font-semibold">Diagnostics</h3>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                  {(
                    [
                      ["Model", exchange.model_id ?? "—"],
                      ["Latency", `${exchange.latency_ms} ms`],
                      [
                        "Answer tokens (in / out)",
                        exchange.input_tokens === null
                          ? "—"
                          : `${exchange.input_tokens} / ${exchange.output_tokens}`,
                      ],
                      [
                        "Passages retrieved",
                        String(exchange.retrieved_passages),
                      ],
                      [
                        "Claims dropped by verification",
                        String(exchange.dropped_claims),
                      ],
                      ["Request ID", exchange.request_id ?? "—"],
                    ] as const
                  ).map(([k, v]) => (
                    <div key={k} className="min-w-0">
                      <dt className="text-muted-foreground text-xs">{k}</dt>
                      <dd className="truncate font-mono text-xs">{v}</dd>
                    </div>
                  ))}
                </dl>
              </section>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

function ModelUsageDetails({
  calls,
}: {
  calls: NonNullable<AdminExchange["model_usage"]>;
}) {
  if (calls.length === 0) return null;
  const incomplete = calls.some(
    (call) =>
      call.input_tokens === null ||
      call.output_tokens === null ||
      call.estimated_cost_usd === null,
  );
  const input = calls.reduce((total, call) => total + (call.input_tokens ?? 0), 0);
  const output = calls.reduce((total, call) => total + (call.output_tokens ?? 0), 0);
  const estimatedCost = calls.reduce(
    (total, call) => total + (call.estimated_cost_usd ?? 0),
    0,
  );
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">Model usage</h3>
      <p className="text-muted-foreground text-xs">
        {calls.length} Bedrock calls · {input} input / {output} output tokens
        {incomplete ? " (partial)" : ""} · about ${estimatedCost.toFixed(6)}
        {incomplete ? " for measured calls" : ""}
      </p>
      <details className="rounded-lg border p-3 text-xs">
        <summary className="cursor-pointer font-medium">View model calls</summary>
        <ol className="mt-3 space-y-2">
          {calls.map((call, index) => (
            <li key={index} className="border-t pt-2 first:border-0 first:pt-0">
              <span className="font-medium">{call.model_id}</span> · {call.operation} · {call.status}
              <br />
              {call.input_tokens ?? "?"} in / {call.output_tokens ?? "?"} out ·{" "}
              {call.latency_ms === null ? "?" : `${call.latency_ms} ms`} ·{" "}
              {call.estimated_cost_usd === null
                ? "cost unknown"
                : `about $${call.estimated_cost_usd.toFixed(6)}`}
            </li>
          ))}
        </ol>
        <p className="text-muted-foreground mt-3">
          Usage estimates exclude credits and other AWS charges. Calls still running
          after a timeout may be billed later.
        </p>
      </details>
    </section>
  );
}
