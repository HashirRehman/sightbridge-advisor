"use client";

import { ArrowUp, Loader2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export const MAX_QUESTION_CHARS = 1000;

export function Composer({
  onSubmit,
  pending,
}: {
  onSubmit: (question: string) => void;
  pending: boolean;
}) {
  const [value, setValue] = useState("");
  const trimmed = value.trim();
  const tooLong = value.length > MAX_QUESTION_CHARS;
  const canSend = trimmed.length >= 3 && !tooLong && !pending;

  function submit() {
    if (!canSend) return;
    onSubmit(trimmed);
    setValue("");
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className="bg-card focus-within:border-foreground/40 rounded-xl border p-2 shadow-sm transition-colors"
    >
      <label htmlFor="question" className="sr-only">
        Ask about 1031 exchanges, DSTs or 721 exchanges
      </label>
      <Textarea
        id="question"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder="Ask about 1031 exchanges, DSTs or 721 exchanges…"
        rows={2}
        aria-describedby="question-help"
        aria-invalid={tooLong}
        className="max-h-48 min-h-[3.25rem] resize-none border-0 bg-transparent px-2 shadow-none focus-visible:ring-0"
      />
      <div className="flex items-center justify-between gap-3 px-2 pt-1">
        <p id="question-help" className="text-muted-foreground text-xs">
          <span className={tooLong ? "text-destructive font-medium" : undefined}>
            {value.length}/{MAX_QUESTION_CHARS}
          </span>
          <span className="hidden sm:inline"> · Enter to send, Shift+Enter for a new line</span>
        </p>
        <Button type="submit" size="icon" disabled={!canSend} aria-label="Send question">
          {pending ? <Loader2 className="animate-spin" aria-hidden /> : <ArrowUp aria-hidden />}
        </Button>
      </div>
    </form>
  );
}
