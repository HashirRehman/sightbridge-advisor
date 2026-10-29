import { ChevronRight, MessageSquare, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { ConversationSummary } from "@/lib/api";
import { cn } from "@/lib/utils";

const dateFormat = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

export function ConversationList({
  conversations,
  loading,
  activeId,
  onSelect,
  onNew,
}: {
  conversations: ConversationSummary[];
  loading: boolean;
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col gap-4 overflow-hidden">
      <Button variant="outline" className="justify-start" onClick={onNew}>
        <Plus aria-hidden /> New conversation
      </Button>
      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto overscroll-contain">
        <h2 className="text-muted-foreground px-2 pb-1 text-xs font-semibold tracking-wide uppercase">
          History
        </h2>
        {loading ? (
          <div className="space-y-2 px-2" aria-label="Loading conversations">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : conversations.length === 0 ? (
          <p className="text-muted-foreground px-2 text-sm">Your questions will appear here.</p>
        ) : (
          <ul className="space-y-0.5">
            {conversations.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  onClick={() => onSelect(c.id)}
                  aria-current={c.id === activeId ? "true" : undefined}
                  className={cn(
                    "flex w-full cursor-pointer items-start gap-2.5 rounded-md px-2 py-2 text-left text-sm transition-colors",
                    c.id === activeId ? "bg-secondary font-medium" : "hover:bg-muted",
                  )}
                >
                  <MessageSquare className="text-muted-foreground mt-0.5 size-4 shrink-0" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{c.title}</span>
                    <span className="text-muted-foreground block text-xs">
                      {dateFormat.format(new Date(c.updated_at))} · {c.exchange_count}{" "}
                      {c.exchange_count === 1 ? "question" : "questions"}
                    </span>
                  </span>
                  <ChevronRight className="text-muted-foreground mt-0.5 size-4 shrink-0" aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
