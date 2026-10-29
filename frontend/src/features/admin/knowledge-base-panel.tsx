"use client";

import { ArrowUpRight, ExternalLink } from "lucide-react";
import { useEffect, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  api,
  type DocumentChunks,
  type IngestedDocument,
  type KnowledgeBase,
} from "@/lib/api";

const date = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

export function KnowledgeBasePanel() {
  const [data, setData] = useState<KnowledgeBase | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<IngestedDocument | null>(null);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .knowledgeBase()
      .then((value) => {
        if (!cancelled) setData(value);
      })
      .catch(() => {
        if (!cancelled)
          setError(
            "Couldn't load the ingested documents. Reload the page to try again.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section
      className="space-y-6 lg:flex lg:min-h-0 lg:flex-1 lg:flex-col"
      aria-label="Knowledge base"
    >
      <div>
        <h2 className="text-lg font-semibold">
          Ingested documents & embeddings
        </h2>
        <p className="text-muted-foreground mt-1 text-sm">
          The sources currently stored for answering questions.
        </p>
      </div>
      {error && (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {!data && !error && (
        <Skeleton className="h-40 w-full" aria-label="Loading knowledge base" />
      )}
      {data && (
        <>
          <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              ["Ingested documents", data.document_count],
              ["Embedded chunks", data.chunk_count],
              ["Answer model", data.answer_model],
              ["Fallback model", data.fallback_answer_model ?? "None"],
            ].map(([label, value]) => (
              <div
                key={label}
                className="bg-card min-w-0 rounded-xl border p-4"
              >
                <dt className="text-muted-foreground text-xs">{label}</dt>
                <dd className="mt-2 text-sm font-semibold break-words">
                  {value}
                </dd>
              </div>
            ))}
          </dl>
          <div className="bg-card space-y-3 rounded-xl border p-4">
            <h3 className="text-sm font-semibold">Embedding models</h3>
            <p className="text-muted-foreground text-sm break-words">
              Current query model:{" "}
              <span className="font-medium text-foreground">
                {data.query_embedding_model}
              </span>
            </p>
            {data.embeddings.length === 0 ? (
              <p className="text-muted-foreground text-sm">
                No embeddings have been stored yet.
              </p>
            ) : (
              <ul className="space-y-2">
                {data.embeddings.map((e) => (
                  <li
                    key={`${e.model_id}-${e.dimensions}`}
                    className="flex flex-wrap justify-between gap-2 rounded-lg bg-muted/50 p-3 text-sm"
                  >
                    <span className="min-w-0 font-medium break-all">
                      {e.model_id}
                    </span>
                    <span className="text-muted-foreground">
                      {e.chunk_count} chunks · {e.dimensions} dimensions
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {data.documents.length === 0 ? (
            <p className="text-muted-foreground rounded-xl border p-8 text-center">
              No documents have been ingested yet.
            </p>
          ) : (
            // The summary above stays in view; only the documents scroll (desktop). The list
            // keeps a usable minimum height, so very short screens scroll the page instead.
            // `relative` keeps screen-reader-only text inside the scroll area; focusable so
            // keyboard users can scroll it too.
            <div
              tabIndex={0}
              aria-label="Ingested documents, scrollable"
              className="lg:relative lg:-mx-1 lg:min-h-80 lg:flex-1 lg:overflow-y-auto lg:px-1 lg:pb-1"
            >
              <ul
                className="grid gap-3 md:grid-cols-2"
                aria-label="Ingested documents"
              >
                {data.documents.map((doc) => (
                  <li
                    key={doc.id}
                    className="bg-card flex min-w-0 flex-col gap-3 rounded-xl border p-4"
                  >
                    <div className="min-w-0">
                      <h3 className="font-semibold leading-snug">
                        {doc.title}
                      </h3>
                      <p className="text-muted-foreground mt-1 text-xs">
                        {doc.publisher} · {doc.document_type.toUpperCase()}
                      </p>
                    </div>
                    <p className="text-muted-foreground text-sm">
                      {doc.chunk_count} embedded chunks · Updated{" "}
                      {date.format(new Date(doc.updated_at))}
                    </p>
                    {doc.topics.length > 0 && (
                      <p className="text-muted-foreground text-xs">
                        Topics: {doc.topics.join(", ")}
                      </p>
                    )}
                    <div className="mt-auto flex flex-wrap items-center gap-4">
                      <button
                        type="button"
                        className="inline-flex cursor-pointer items-center gap-1 text-sm font-medium underline underline-offset-4"
                        onClick={() => setSelected(doc)}
                        aria-haspopup="dialog"
                      >
                        View chunks & embeddings{" "}
                        <ArrowUpRight className="size-4" aria-hidden />
                        <span className="sr-only"> for {doc.title}</span>
                      </button>
                      <a
                        href={doc.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-sm underline underline-offset-4"
                      >
                        Original source{" "}
                        <ExternalLink className="size-3.5" aria-hidden />
                        <span className="sr-only">
                          {" "}
                          for {doc.title} (opens in a new tab)
                        </span>
                      </a>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
      <Sheet
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
      >
        <SheetContent className="overflow-y-auto data-[side=right]:w-full data-[side=right]:sm:max-w-2xl">
          {selected && <ChunkDetails key={selected.id} document={selected} />}
        </SheetContent>
      </Sheet>
    </section>
  );
}

function ChunkDetails({ document }: { document: IngestedDocument }) {
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<DocumentChunks | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    api
      .documentChunks(document.id, offset)
      .then((value) => {
        if (!cancelled) setData(value);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [document.id, offset]);
  return (
    <>
      <SheetHeader>
        <SheetTitle className="pr-6 leading-snug">{document.title}</SheetTitle>
        <SheetDescription>
          Stored source passages and their embedding metadata.
        </SheetDescription>
      </SheetHeader>
      <div className="space-y-4 px-4 pb-8">
        {error ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>
              Couldn&apos;t load these chunks. Close this panel and try again.
            </AlertDescription>
          </Alert>
        ) : loading ? (
          <Skeleton className="h-40 w-full" aria-label="Loading chunks" />
        ) : (
          data && (
            <>
              <p className="text-muted-foreground text-sm">
                {data.total === 0
                  ? "No chunks stored."
                  : `Showing ${data.offset + 1}–${Math.min(data.offset + data.items.length, data.total)} of ${data.total} chunks`}
              </p>
              {data.items.map((chunk) => (
                <article
                  key={chunk.id}
                  className="space-y-3 rounded-xl border p-4"
                  aria-label={`Chunk ${chunk.chunk_index + 1}`}
                >
                  <h3 className="text-sm font-semibold">
                    Chunk {chunk.chunk_index + 1}
                    {chunk.page_number !== null &&
                      ` · Page ${chunk.page_number}`}
                    {chunk.section && ` · ${chunk.section}`}
                  </h3>
                  <p className="text-sm leading-relaxed whitespace-pre-wrap break-words">
                    {chunk.content}
                  </p>
                  <dl className="space-y-1 border-t pt-3 text-xs">
                    <div>
                      <dt className="text-muted-foreground inline">
                        Embedding model:{" "}
                      </dt>
                      <dd className="inline break-all">
                        {chunk.embedding_model}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground inline">
                        Dimensions:{" "}
                      </dt>
                      <dd className="inline">{chunk.dimensions}</dd>
                    </div>
                  </dl>
                  <details className="text-xs">
                    <summary className="cursor-pointer font-medium">
                      Vector preview (first {chunk.embedding_preview.length} of{" "}
                      {chunk.dimensions} values)
                    </summary>
                    <p className="text-muted-foreground mt-2 font-mono leading-relaxed break-all">
                      [
                      {chunk.embedding_preview
                        .map((v) => v.toFixed(6))
                        .join(", ")}
                      , …]
                    </p>
                  </details>
                </article>
              ))}
              <div className="flex justify-between gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={offset === 0}
                  onClick={() => setOffset((v) => Math.max(0, v - 10))}
                >
                  Previous chunks
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={offset + data.limit >= data.total}
                  onClick={() => setOffset((v) => v + 10)}
                >
                  Next chunks
                </Button>
              </div>
            </>
          )
        )}
      </div>
    </>
  );
}
