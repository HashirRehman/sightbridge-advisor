// Browser-side API client. Calls go through the same-origin Next.js proxy, which attaches the
// session token server-side; the browser never sees a token.

export type ExchangeStatus = "answered" | "refused" | "unsupported" | "failed";

export type Claim = { text: string; citations: number[] };

export type Citation = {
  number: number;
  document_id: string;
  title: string;
  publisher: string;
  url: string;
  source_url: string;
  location: string | null;
  quotes: string[];
};

export type Exchange = {
  activity?: ActivityStep[];
  model_usage?: ModelCallUsage[];
  id: string;
  conversation_id: string;
  question: string;
  status: ExchangeStatus;
  reason: string | null;
  notice: string | null;
  explanation: Claim[];
  key_considerations: Claim[];
  citations: Citation[];
  disclaimer: string;
  created_at: string;
};

export type ModelCallUsage = {
  model_id: string;
  operation: string;
  region: string;
  status: "running" | "completed" | "failed";
  input_tokens: number | null;
  output_tokens: number | null;
  latency_ms: number | null;
  estimated_cost_usd: number | null;
  price_reference: string | null;
};

export type ActivityStep = {
  id: number;
  kind:
    | "policy"
    | "safety"
    | "embedding"
    | "retrieval"
    | "generation"
    | "model_call"
    | "verification"
    | "output_check"
    | "privacy_check"
    | "understanding"
    | "search_refinement";
  status: "running" | "completed" | "failed";
  started_ms: number;
  finished_ms: number | null;
  model_id: string | null;
  source_count: number | null;
};
export type AnswerActivity = {
  id: string;
  steps: ActivityStep[];
  finished: boolean;
  exchange_id: string | null;
};

export type ConversationSummary = {
  id: string;
  title: string;
  exchange_count: number;
  updated_at: string;
};

export type ConversationDetail = {
  id: string;
  title: string;
  exchanges: Exchange[];
};

export type AdminConversation = ConversationDetail & { user_email: string };

export type Me = { user_id: string; email: string; is_admin: boolean };

export type AdminExchange = Exchange & {
  user_id: string;
  user_email: string;
  answer_text: string;
  model_id: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  latency_ms: number;
  retrieved_passages: number;
  dropped_claims: number;
  request_id: string | null;
};

export type AuditPage = {
  items: AdminExchange[];
  total: number;
  limit: number;
  offset: number;
  summary: Record<"total" | ExchangeStatus, number>;
};

export type EmbeddingSummary = {
  model_id: string;
  dimensions: number;
  chunk_count: number;
};
export type IngestedDocument = {
  id: string;
  title: string;
  publisher: string;
  source_url: string;
  document_type: string;
  topics: string[];
  fetched_at: string;
  updated_at: string;
  chunk_count: number;
  embeddings: EmbeddingSummary[];
};
export type KnowledgeBase = {
  document_count: number;
  chunk_count: number;
  query_embedding_model: string;
  answer_model: string;
  fallback_answer_model: string | null;
  embeddings: EmbeddingSummary[];
  documents: IngestedDocument[];
};
export type DocumentChunks = {
  document_id: string;
  title: string;
  total: number;
  offset: number;
  limit: number;
  items: {
    id: string;
    chunk_index: number;
    content: string;
    page_number: number | null;
    section: string | null;
    embedding_model: string;
    dimensions: number;
    embedding_preview: number[];
  }[];
};

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

function messageFor(status: number, detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (status === 422)
    return "Please enter a question between 3 and 1,000 characters.";
  return "Something went wrong. Please try again.";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/proxy/${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      0,
      "Can't reach the server. Check your connection and try again.",
    );
  }
  if (response.status === 401) {
    window.location.assign("/login");
    throw new ApiError(401, "Your session has expired. Please sign in again.");
  }
  const body = (await response.json().catch(() => ({}))) as {
    detail?: unknown;
  };
  if (!response.ok)
    throw new ApiError(
      response.status,
      messageFor(response.status, body.detail),
    );
  return body as T;
}

export const api = {
  me: () => request<Me>("me"),
  ask: async (
    question: string,
    conversationId?: string,
    activityId?: string,
    signal?: AbortSignal,
  ) => {
    const response = await request<Exchange | AnswerActivity>("chat", {
      method: "POST",
      signal,
      body: JSON.stringify({
        question,
        conversation_id: conversationId ?? null,
        activity_id: activityId ?? null,
        background: true,
      }),
    });
    if ("conversation_id" in response) return response;
    const expires = Date.now() + 130_000;
    while (Date.now() < expires) {
      if (signal?.aborted)
        throw new ApiError(
          0,
          "Request interrupted. You can recover it by trying again.",
        );
      try {
        return await request<Exchange>(`chat/activity/${response.id}/result`, {
          signal,
        });
      } catch (e) {
        if (!(e instanceof ApiError) || e.status !== 409) throw e;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1400));
    }
    throw new ApiError(
      410,
      "This request could not finish. Please send your question again.",
    );
  },
  activity: (id: string, signal: AbortSignal) =>
    request<AnswerActivity>(`chat/activity/${id}`, { signal }),
  conversations: () => request<ConversationSummary[]>("conversations"),
  conversation: (id: string) =>
    request<ConversationDetail>(`conversations/${id}`),
  auditLog: (params: URLSearchParams) =>
    request<AuditPage>(`admin/exchanges?${params}`),
  knowledgeBase: () => request<KnowledgeBase>("admin/knowledge-base"),
  adminConversation: (id: string) =>
    request<AdminConversation>(`admin/conversations/${id}`),
  documentChunks: (id: string, offset = 0) =>
    request<DocumentChunks>(
      `admin/documents/${encodeURIComponent(id)}/chunks?offset=${offset}&limit=10`,
    ),
};
