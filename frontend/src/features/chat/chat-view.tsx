"use client";

import { AlertCircle, History, RotateCcw } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { AppHeader } from "@/components/app-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError, type ConversationSummary, type Exchange } from "@/lib/api";

import { AnswerCard } from "./answer-card";
import { Composer } from "./composer";
import { ConversationList } from "./conversation-list";
import { SuggestedQuestions } from "./suggested-questions";

const TOPICS = ["1031 exchanges", "Delaware Statutory Trusts", "721 exchanges & UPREITs"];

export function ChatView() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const conversationId = searchParams.get("c");

  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [threadLoading, setThreadLoading] = useState(false);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [error, setError] = useState<{ message: string; retry?: string } | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const messagesRef = useRef<HTMLDivElement>(null);
  // The thread we just created locally — don't refetch it when the URL catches up.
  const localConversation = useRef<string | null>(null);

  const refreshConversations = useCallback(() => {
    api
      .conversations()
      .then(setConversations)
      .catch(() => undefined)
      .finally(() => setListLoading(false));
  }, []);

  useEffect(refreshConversations, [refreshConversations]);

  useEffect(() => {
    setError(null);
    if (!conversationId) {
      setExchanges([]);
      return;
    }
    if (conversationId === localConversation.current) return;
    setThreadLoading(true);
    api
      .conversation(conversationId)
      .then((c) => setExchanges(c.exchanges))
      .catch((e: unknown) => {
        setExchanges([]);
        setError({ message: e instanceof ApiError ? e.message : "Couldn't load this conversation." });
      })
      .finally(() => setThreadLoading(false));
  }, [conversationId]);

  useEffect(() => {
    const messages = messagesRef.current;
    if (!messages) return;
    const exchanges = messages.querySelectorAll<HTMLElement>("[data-exchange]");
    const latest = error
      ? messages.querySelector<HTMLElement>("[data-chat-error]")
      : pendingQuestion
        ? messages.querySelector<HTMLElement>("[data-pending-question]")
        : exchanges.item(exchanges.length - 1);
    // Keep the new question and explanation in view, even when its sources are long.
    messages.scrollTo({
      top: latest ? Math.max(0, latest.offsetTop - 24) : 0,
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth",
    });
  }, [exchanges.length, pendingQuestion, error]);

  async function ask(question: string) {
    setError(null);
    setPendingQuestion(question);
    try {
      const exchange = await api.ask(question, conversationId ?? localConversation.current ?? undefined);
      setExchanges((prev) => [...prev, exchange]);
      if (exchange.conversation_id !== conversationId) {
        localConversation.current = exchange.conversation_id;
        router.replace(`/?c=${exchange.conversation_id}`, { scroll: false });
      }
      refreshConversations();
    } catch (e) {
      const message = e instanceof ApiError ? e.message : "Something went wrong. Please try again.";
      setError({ message, retry: question });
    } finally {
      setPendingQuestion(null);
    }
  }

  function openConversation(id: string | null) {
    localConversation.current = null;
    setHistoryOpen(false);
    router.push(id ? `/?c=${id}` : "/", { scroll: false });
  }

  const history = (
    <ConversationList
      conversations={conversations}
      loading={listLoading}
      activeId={conversationId}
      onSelect={openConversation}
      onNew={() => openConversation(null)}
    />
  );
  const empty = exchanges.length === 0 && !pendingQuestion && !threadLoading;

  return (
    <div className="flex h-dvh min-h-0 flex-col overflow-hidden">
      <AppHeader
        leading={
          <Sheet open={historyOpen} onOpenChange={setHistoryOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Conversation history">
                <History aria-hidden />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-[85vw] max-w-xs p-4">
              <SheetHeader className="p-0 pb-2">
                <SheetTitle>Conversations</SheetTitle>
              </SheetHeader>
              {history}
            </SheetContent>
          </Sheet>
        }
      />
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <aside className="bg-card hidden min-h-0 w-72 shrink-0 overflow-hidden border-r p-4 lg:block" aria-label="Conversations">
          {history}
        </aside>

        <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          <div
            ref={messagesRef}
            role="region"
            aria-label="Conversation messages"
            tabIndex={0}
            className="relative min-h-0 flex-1 overflow-y-auto overscroll-contain"
          >
            <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-8 sm:px-6">
              {empty && (
                <section className="space-y-7 pt-2 sm:pt-8">
                  <div className="space-y-3">
                    <h1 className="text-[1.75rem] leading-tight font-semibold sm:text-3xl">
                      What would you like to understand today?
                    </h1>
                    <p className="text-muted-foreground max-w-2xl leading-relaxed">
                      Educational answers grounded in IRS, SEC and FINRA material and other vetted
                      public sources, with the exact passages cited. No personalised tax, legal or
                      investment advice.
                    </p>
                    <ul className="flex flex-wrap gap-2 pt-1" aria-label="Supported topics">
                      {TOPICS.map((t) => (
                        <li key={t} className="bg-sand/60 rounded-full px-3 py-1 text-xs font-medium">
                          {t}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <SuggestedQuestions onSelect={ask} disabled={pendingQuestion !== null} />
                </section>
              )}

              {threadLoading && (
                <div className="space-y-3" aria-label="Loading conversation">
                  <Skeleton className="ml-auto h-10 w-2/3" />
                  <Skeleton className="h-40 w-full" />
                </div>
              )}

              {exchanges.map((exchange) => (
                <div key={exchange.id} data-exchange className="space-y-3">
                  <UserQuestion text={exchange.question} />
                  <AnswerCard exchange={exchange} />
                </div>
              ))}

              {pendingQuestion && (
                <div data-pending-question className="space-y-3">
                  <UserQuestion text={pendingQuestion} />
                  <div className="bg-card space-y-3 rounded-xl border p-5" role="status" aria-live="polite">
                    <p className="text-muted-foreground text-sm">
                      Searching approved sources and checking citations…
                    </p>
                    <Skeleton className="h-4 w-11/12" />
                    <Skeleton className="h-4 w-4/5" />
                    <Skeleton className="h-4 w-2/3" />
                  </div>
                </div>
              )}

              {error && (
                <Alert data-chat-error variant="destructive" role="alert">
                  <AlertCircle aria-hidden />
                  <AlertDescription className="flex flex-wrap items-center gap-3">
                    <span>{error.message}</span>
                    {error.retry && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => error.retry && ask(error.retry)}
                      >
                        <RotateCcw aria-hidden /> Try again
                      </Button>
                    )}
                  </AlertDescription>
                </Alert>
              )}
            </div>
          </div>

          <div className="bg-background/95 shrink-0 border-t px-4 py-3 backdrop-blur sm:px-6">
            <div className="mx-auto w-full max-w-3xl">
              <Composer onSubmit={ask} pending={pendingQuestion !== null} />
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

function UserQuestion({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <p className="bg-primary text-primary-foreground max-w-[85%] rounded-2xl rounded-br-md px-4 py-2.5 text-[0.95rem] leading-relaxed break-words">
        <span className="sr-only">You asked: </span>
        {text}
      </p>
    </div>
  );
}
