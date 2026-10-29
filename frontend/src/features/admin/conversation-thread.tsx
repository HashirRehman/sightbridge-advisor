"use client";

import { AlertCircle, Eye } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError, type AdminConversation } from "@/lib/api";
import { cn } from "@/lib/utils";

import { AnswerCard } from "../chat/answer-card";
import { UserQuestion } from "../chat/user-question";

/** A read-only copy of any user's thread, scrolled to the audited exchange. */
export function ConversationThread({
  conversationId,
  highlightedId,
}: {
  conversationId: string;
  highlightedId: string;
}) {
  const [conversation, setConversation] = useState<AdminConversation | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const highlighted = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setConversation(null);
    setError(null);
    api
      .adminConversation(conversationId)
      .then((value) => !cancelled && setConversation(value))
      .catch(
        (e: unknown) =>
          !cancelled &&
          setError(
            e instanceof ApiError && e.status === 404
              ? "This conversation no longer exists."
              : "Couldn't load this conversation.",
          ),
      );
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  useEffect(() => {
    // Scroll only the drawer body: scrollIntoView would also move the fixed header.
    const target = highlighted.current;
    const body = target?.closest<HTMLElement>("[data-scroll-body]");
    if (target && body) {
      body.scrollTop +=
        target.getBoundingClientRect().top -
        body.getBoundingClientRect().top -
        16;
    }
  }, [conversation]);

  if (error) {
    return (
      <Alert variant="destructive" role="alert">
        <AlertCircle aria-hidden />
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }
  if (!conversation) {
    return (
      <div className="space-y-3" aria-label="Loading conversation">
        <Skeleton className="ml-auto h-10 w-1/2" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  return (
    <div className="space-y-6">
      <p className="text-muted-foreground flex flex-wrap items-center gap-2 text-xs">
        <span>{conversation.title}</span>
        <span aria-hidden>·</span>
        <span>
          {conversation.exchanges.length}{" "}
          {conversation.exchanges.length === 1 ? "question" : "questions"}
        </span>
        <span className="bg-secondary text-foreground inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-medium">
          <Eye className="size-3" aria-hidden /> Read-only
        </span>
      </p>
      {conversation.exchanges.map((exchange) => {
        const current = exchange.id === highlightedId;
        return (
          <div
            key={exchange.id}
            ref={current ? highlighted : undefined}
            aria-current={current ? "true" : undefined}
            className={cn(
              "space-y-3 rounded-2xl",
              current &&
                "ring-ring/40 ring-offset-background ring-2 ring-offset-4",
            )}
          >
            <UserQuestion text={exchange.question} label="The user asked" />
            <AnswerCard exchange={exchange} />
          </div>
        );
      })}
    </div>
  );
}
