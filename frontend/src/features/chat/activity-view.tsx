import { Check, ChevronDown, LoaderCircle, X } from "lucide-react";

import type { ActivityStep } from "@/lib/api";

const LABELS: Record<ActivityStep["kind"], string> = {
  understanding: "Understanding your question in context",
  search_refinement: "Trying another document search",
  policy: "Checking the request",
  safety: "Reviewing the request for safety",
  embedding: "Preparing document search",
  retrieval: "Searching approved sources",
  generation: "Preparing the explanation",
  model_call: "Requesting an answer from the model",
  verification: "Checking supporting evidence",
  output_check: "Reviewing the response for safety",
  privacy_check: "Checking for private details",
};
function modelName(id: string) {
  if (id.includes("nova-lite")) return "Amazon Nova Lite";
  if (id.includes("deepseek")) return "DeepSeek V3.2";
  if (id.includes("titan-embed-text-v2")) return "Titan Text Embeddings V2";
  return id;
}

export function ActivityView({
  steps,
  pending = false,
  unavailable = false,
}: {
  steps: ActivityStep[];
  pending?: boolean;
  unavailable?: boolean;
}) {
  if (!pending && steps.length === 0) return null;
  const latest = steps.at(-1);
  return (
    <div
      className="text-muted-foreground space-y-2 text-xs"
      aria-label="Answer activity"
    >
      {pending && (
        <p
          role="status"
          aria-live="polite"
          className="flex items-center gap-2 text-sm"
        >
          <LoaderCircle
            className="size-4 animate-spin motion-reduce:animate-none"
            aria-hidden
          />
          {latest ? LABELS[latest.kind] : "Waiting for the service…"}
        </p>
      )}
      {unavailable && pending && (
        <p>
          Live activity is temporarily unavailable. Your question is still being
          processed.
        </p>
      )}
      {steps.length > 0 && (
        <details open={pending || undefined} className="group">
          <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-sm font-medium">
            <ChevronDown
              className="size-3.5 transition-transform group-open:rotate-180"
              aria-hidden
            />
            {pending ? "Activity so far" : "View activity"}
          </summary>
          <p className="mt-2 text-xs">
            These are service steps, not the model&apos;s private reasoning.
          </p>
          <ol className="mt-3 space-y-2.5" aria-label="Service steps">
            {steps.map((step) => (
              <li key={step.id} className="flex items-start gap-2">
                {step.status === "completed" ? (
                  <Check
                    className="text-success mt-0.5 size-3.5 shrink-0"
                    aria-hidden
                  />
                ) : step.status === "failed" ? (
                  <X
                    className="text-warning mt-0.5 size-3.5 shrink-0"
                    aria-hidden
                  />
                ) : (
                  <LoaderCircle
                    className="mt-0.5 size-3.5 shrink-0 animate-spin motion-reduce:animate-none"
                    aria-hidden
                  />
                )}
                <div className="min-w-0 flex-1">
                  <p className="text-foreground">
                    {LABELS[step.kind]}{" "}
                    <span className="sr-only">
                      —{" "}
                      {step.status === "running" ? "in progress" : step.status}
                    </span>
                  </p>
                  {step.model_id && (
                    <p className="mt-0.5 break-words">
                      {modelName(step.model_id)}
                    </p>
                  )}
                  {step.source_count !== null && (
                    <p>{step.source_count} supporting passages found</p>
                  )}
                </div>
                {step.finished_ms !== null && (
                  <span className="shrink-0 tabular-nums">
                    {Math.max(
                      0,
                      (step.finished_ms - step.started_ms) / 1000,
                    ).toFixed(1)}
                    s
                  </span>
                )}
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}
