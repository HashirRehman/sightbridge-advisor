import { AlertTriangle, ExternalLink, Info, SearchX, ShieldAlert } from "lucide-react";

import { REASON_LABELS, StatusBadge } from "@/components/status-badge";
import type { Claim, Exchange } from "@/lib/api";
import { cn } from "@/lib/utils";
import { ActivityView } from "./activity-view";

function citeId(exchangeId: string, n: number) {
  return `cite-${exchangeId}-${n}`;
}

function ClaimText({ claim, exchangeId }: { claim: Claim; exchangeId: string }) {
  return (
    <>
      {claim.text}
      {claim.citations.map((n) => (
        <a
          key={n}
          href={`#${citeId(exchangeId, n)}`}
          aria-label={`Source ${n}`}
          className="bg-secondary text-foreground hover:bg-sand ml-1 inline-flex h-5 min-w-5 items-center justify-center rounded px-1 align-[0.15em] text-[0.7rem] font-semibold no-underline transition-colors"
        >
          {n}
        </a>
      ))}
    </>
  );
}

const NOTICE_STYLE = {
  refused: { icon: ShieldAlert, className: "border-warning/30 bg-warning/5" },
  unsupported: { icon: SearchX, className: "border-border bg-muted/60" },
  failed: { icon: AlertTriangle, className: "border-destructive/30 bg-destructive/5" },
  answered: { icon: Info, className: "border-border bg-muted/60" },
} as const;

export function AnswerCard({ exchange }: { exchange: Exchange }) {
  const notice = NOTICE_STYLE[exchange.status];
  const NoticeIcon = notice.icon;
  const hasAnswer = exchange.explanation.length > 0;

  return (
    <article
      aria-label="Assistant answer"
      className="bg-card space-y-5 rounded-xl border p-5 shadow-[0_1px_2px_rgba(30,30,36,0.04)] sm:p-6"
    >
      <ActivityView steps={exchange.activity ?? []} />
      {exchange.status !== "answered" && (
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={exchange.status} />
          {exchange.reason && (
            <span className="text-muted-foreground text-xs">
              {REASON_LABELS[exchange.reason] ?? exchange.reason}
            </span>
          )}
        </div>
      )}

      {exchange.notice && exchange.status === "answered" && (
        <p className="text-[0.975rem] leading-7">{exchange.notice}</p>
      )}

      {exchange.notice && exchange.status !== "answered" && (
        <div className={cn("flex gap-3 rounded-lg border p-3.5 text-sm leading-relaxed", notice.className)}>
          <NoticeIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
          <p>{exchange.notice}</p>
        </div>
      )}

      {hasAnswer && (
        <p className="text-[0.975rem] leading-7">
          {exchange.explanation.map((claim, i) => (
            <span key={i}>
              <ClaimText claim={claim} exchangeId={exchange.id} />{" "}
            </span>
          ))}
        </p>
      )}

      {exchange.key_considerations.length > 0 && (
        <section aria-labelledby={`kc-${exchange.id}`} className="space-y-2.5">
          <h3 id={`kc-${exchange.id}`} className="text-sm font-semibold">
            Key considerations
          </h3>
          <ul className="space-y-2">
            {exchange.key_considerations.map((claim, i) => (
              <li key={i} className="flex gap-2.5 text-[0.95rem] leading-6">
                <span className="bg-sand-strong mt-2.5 size-1.5 shrink-0 rounded-full" aria-hidden />
                <span>
                  <ClaimText claim={claim} exchangeId={exchange.id} />
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {exchange.citations.length > 0 && (
        <section aria-labelledby={`src-${exchange.id}`} className="space-y-2.5">
          <h3 id={`src-${exchange.id}`} className="text-sm font-semibold">
            Sources
          </h3>
          <ol className="space-y-2.5">
            {exchange.citations.map((c) => (
              <li
                key={c.number}
                id={citeId(exchange.id, c.number)}
                className="bg-background scroll-mt-24 rounded-lg border p-3.5 target:ring-2 target:ring-[var(--sand-strong)]"
              >
                <div className="flex gap-3">
                  <span className="bg-primary text-primary-foreground flex size-6 shrink-0 items-center justify-center rounded text-xs font-semibold">
                    {c.number}
                  </span>
                  <div className="min-w-0 flex-1 space-y-1.5">
                    <a
                      href={c.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="group inline-flex items-start gap-1.5 text-sm font-semibold underline-offset-4 hover:underline"
                    >
                      <span className="break-words">{c.title}</span>
                      <ExternalLink className="mt-0.5 size-3.5 shrink-0 opacity-60" aria-hidden />
                      <span className="sr-only">(opens the source passage in a new tab)</span>
                    </a>
                    <p className="text-muted-foreground text-xs">
                      {c.publisher}
                      {c.location && <> · {c.location}</>}
                    </p>
                    {c.quotes.map((quote) => (
                      <blockquote
                        key={quote}
                        className="border-sand-strong text-foreground/80 border-l-2 pl-3 text-[0.85rem] leading-relaxed italic"
                      >
                        “{quote}”
                      </blockquote>
                    ))}
                  </div>
                </div>
              </li>
            ))}
          </ol>
        </section>
      )}

      <p className="text-muted-foreground flex gap-2 border-t pt-4 text-xs leading-relaxed">
        <Info className="mt-px size-3.5 shrink-0" aria-hidden />
        <span>{exchange.disclaimer}</span>
      </p>
    </article>
  );
}
