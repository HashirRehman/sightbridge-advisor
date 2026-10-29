import { cn } from "@/lib/utils";

/** Text wordmark. The official Sightbridge logo is deliberately not used in this prototype. */
export function Wordmark({ className, inverted = false }: { className?: string; inverted?: boolean }) {
  return (
    <span className={cn("inline-flex items-baseline gap-2 leading-none", className)}>
      <span
        className={cn(
          "font-heading text-[0.95rem] font-semibold tracking-[0.22em] uppercase",
          inverted ? "text-white" : "text-foreground",
        )}
      >
        Sightbridge
      </span>
      <span
        className={cn(
          "text-[0.8rem] font-medium tracking-wide",
          inverted ? "text-panel-accent" : "text-muted-foreground",
        )}
      >
        Advisor Intelligence
      </span>
    </span>
  );
}
