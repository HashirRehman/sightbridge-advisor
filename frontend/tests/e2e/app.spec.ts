import { expect, test } from "@playwright/test";

import { ADMIN, exchange, json, mockApi, signIn } from "./fixtures";

test("signed-out visitors are sent to the login page", async ({ page }) => {
  await page.goto("/admin");
  await expect(page).toHaveURL(/\/login$/);
});

test("login validates input and reports bad credentials", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Enter a valid email address.")).toBeVisible();

  await page.getByLabel("Work email").fill("advisor@e2e.test");
  await page.getByLabel("Password").fill("wrong-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "Incorrect email or password." }),
  ).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
});

test("a suggested question is submitted and the cited answer is shown", async ({
  page,
}) => {
  let sent: unknown;
  await mockApi(page, {
    chat: async (route) => {
      sent = route.request().postDataJSON();
      await json(route, exchange());
    },
  });
  await signIn(page);

  await page
    .getByRole("list", { name: "Suggested questions" })
    .getByRole("button", { name: /What is a Delaware Statutory Trust\?/ })
    .click();

  const answer = page.getByRole("article", { name: "Assistant answer" });
  await expect(answer).toContainText(
    "recognized as an entity separate from its owners",
  );
  expect(sent).toEqual({
    question: "What is a Delaware Statutory Trust?",
    conversation_id: null,
    activity_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
    background: true,
  });

  const source = answer.getByRole("link", { name: /Revenue Ruling 2004-86/ });
  await expect(source).toHaveAttribute(
    "href",
    "https://home.treasury.gov/rr0486.pdf#page=2",
  );
  await expect(source).toHaveAttribute("target", "_blank");
  await expect(
    answer.getByRole("link", { name: "Source 1" }).first(),
  ).toHaveAttribute("href", /#cite-.+-1$/);
  await expect(answer).toContainText("Educational information only");
  await expect(page).toHaveURL(/\?c=11111111-1111-4111-8111-111111111111/);
});

test("refusals show the outcome, the reason and the disclaimer", async ({
  page,
}) => {
  await mockApi(page, {
    chat: (route) =>
      json(
        route,
        exchange({
          question: "Should I invest my $500,000 in a DST?",
          status: "refused",
          reason: "personalized_advice",
          notice:
            "I can't make recommendations or assess a specific person's situation.",
          explanation: [],
          key_considerations: [],
          citations: [],
        }),
      ),
  });
  await signIn(page);
  await page
    .getByLabel(/Ask about/)
    .fill("Should I invest my $500,000 in a DST?");
  await page.keyboard.press("Enter");

  const answer = page.getByRole("article", { name: "Assistant answer" });
  await expect(answer.getByText("Refused")).toBeVisible();
  await expect(answer.getByText("Personalised advice declined")).toBeVisible();
  await expect(answer).toContainText("I can't make recommendations");
  await expect(answer).toContainText("Educational information only");
});

test("rate limiting and outages show a recoverable error", async ({ page }) => {
  let calls = 0;
  await mockApi(page, {
    chat: async (route) => {
      calls += 1;
      if (calls === 1) {
        await json(
          route,
          {
            detail:
              "Too many questions in a short period. Please wait and try again.",
          },
          429,
        );
      } else {
        await json(route, exchange());
      }
    },
  });
  await signIn(page);
  await page.getByLabel(/Ask about/).fill("How does a 1031 exchange work?");
  await page.keyboard.press("Enter");

  await expect(
    page.getByRole("alert").filter({ hasText: "Too many questions" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(
    page.getByRole("article", { name: "Assistant answer" }),
  ).toBeVisible();
});

test("advisors get no audit navigation and are blocked from the audit page", async ({
  page,
}) => {
  await mockApi(page);
  await signIn(page);
  await expect(
    page.getByRole("navigation", { name: "Main" }),
  ).not.toContainText("Admin dashboard");

  await page.goto("/admin");
  await expect(page.getByText("Admin access required")).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(0);
});

test("admins can review and filter the audit log", async ({ page }) => {
  const requested: string[] = [];
  await mockApi(page, {
    isAdmin: true,
    admin: async (route) => {
      requested.push(new URL(route.request().url()).search);
      await json(route, {
        items: [
          {
            ...exchange(),
            user_id: "u1",
            user_email: "advisor@e2e.test",
            answer_text: "…",
            model_id: "m",
            input_tokens: 1,
            output_tokens: 1,
            latency_ms: 900,
            retrieved_passages: 6,
            dropped_claims: 0,
            request_id: "r1",
          },
        ],
        total: 1,
        limit: 25,
        offset: 0,
        summary: {
          total: 7,
          answered: 4,
          refused: 2,
          unsupported: 1,
          failed: 0,
        },
      });
    },
  });
  await signIn(page, ADMIN);
  await page.getByRole("link", { name: "Admin dashboard" }).click();

  await expect(page.getByRole("table")).toContainText("advisor@e2e.test");
  await expect(page.getByText("Total exchanges").locator("..")).toContainText(
    "7",
  );

  await page.getByLabel("Outcome").click();
  const trigger = await page
    .getByLabel("Outcome", { exact: true })
    .boundingBox();
  const menu = await page.getByRole("listbox").boundingBox();
  expect(trigger).not.toBeNull();
  expect(menu).not.toBeNull();
  expect(
    menu!.y >= trigger!.y + trigger!.height ||
      menu!.y + menu!.height <= trigger!.y,
  ).toBe(true);
  expect(menu!.x + menu!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  await page.getByRole("option", { name: "Refused" }).click();
  await expect.poll(() => requested.at(-1)).toContain("status=refused");

  const question = page.getByRole("button", {
    name: "What is a Delaware Statutory Trust?",
  });
  await expect(question.locator("svg")).toBeVisible();
  await expect(question).toHaveAttribute("aria-haspopup", "dialog");
  await question.click();
  await expect(page.getByText("Answer shown to the user")).toBeVisible();
});

test("capabilities answers show help without a refusal or fabricated sources", async ({
  page,
}) => {
  await mockApi(page, {
    chat: (route) =>
      json(
        route,
        exchange({
          question: "What kind of information you can give to me",
          reason: "capabilities",
          notice:
            "I can help you understand 1031 exchanges, DSTs, and 721 exchanges. For example: How does a 1031 exchange work?",
          explanation: [],
          key_considerations: [],
          citations: [],
        }),
      ),
  });
  await signIn(page);
  await page
    .getByLabel(/Ask about/)
    .fill("What kind of information you can give to me");
  await page.getByRole("button", { name: "Send question" }).click();
  const answer = page.getByRole("article", { name: "Assistant answer" });
  await expect(answer).toContainText("I can help you understand");
  await expect(answer).toContainText("Educational information only");
  await expect(answer.getByText("Refused", { exact: true })).toHaveCount(0);
  await expect(answer.getByRole("heading", { name: "Sources" })).toHaveCount(0);
});

test("long conversations scroll inside the message panel with the composer visible", async ({
  page,
}) => {
  await mockApi(page);
  const id = "11111111-1111-4111-8111-111111111111";
  await page.route(`**/api/proxy/conversations/${id}`, (route) =>
    json(route, {
      id,
      title: "Long conversation",
      exchanges: Array.from({ length: 12 }, () => exchange()),
    }),
  );
  await signIn(page);
  await page.goto(`/?c=${id}`);
  const messages = page.getByRole("region", { name: "Conversation messages" });
  await expect(messages.getByRole("article")).toHaveCount(12);
  await messages.evaluate((element) => {
    element.scrollTop = 0;
  });
  const rect = await messages.boundingBox();
  await page.mouse.move(rect!.x + rect!.width / 2, rect!.y + 40);
  await page.mouse.wheel(0, 500);
  await expect
    .poll(() => messages.evaluate((element) => element.scrollTop))
    .toBeGreaterThan(0);
  const metrics = await page.evaluate(() => ({
    scrollTop: window.scrollY,
    documentHeight: document.documentElement.scrollHeight,
    viewportHeight: window.innerHeight,
  }));
  expect(metrics.scrollTop).toBe(0);
  expect(metrics.documentHeight).toBeLessThanOrEqual(
    metrics.viewportHeight + 1,
  );
  const composer = await page.getByLabel(/Ask about/).boundingBox();
  expect(composer!.y).toBeGreaterThan(0);
  expect(composer!.y + composer!.height).toBeLessThanOrEqual(
    metrics.viewportHeight,
  );
});

test("the chat layout never scrolls horizontally", async ({ page }) => {
  await mockApi(page);
  await signIn(page);
  await page.getByLabel(/Ask about/).fill("What is a DST?");
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("article", { name: "Assistant answer" }),
  ).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(overflow).toBe(false);
});

test("admins can inspect ingested documents, passages and embedding metadata", async ({
  page,
}) => {
  await mockApi(page, {
    isAdmin: true,
    admin: (route) =>
      json(route, {
        items: [],
        total: 0,
        limit: 25,
        offset: 0,
        summary: {
          total: 0,
          answered: 0,
          refused: 0,
          unsupported: 0,
          failed: 0,
        },
      }),
  });
  const embedding = {
    model_id: "amazon.titan-embed-text-v2:0",
    dimensions: 1024,
    chunk_count: 2,
  };
  await page.route("**/api/proxy/admin/knowledge-base", (route) =>
    json(route, {
      document_count: 1,
      chunk_count: 2,
      query_embedding_model: embedding.model_id,
      answer_model: "deepseek.v3.2",
      fallback_answer_model: "apac.amazon.nova-lite-v1:0",
      embeddings: [embedding],
      documents: [
        {
          id: "irs-ruling",
          title: "Revenue Ruling 2004-86",
          publisher: "IRS",
          document_type: "pdf",
          source_url: "https://www.irs.gov/ruling.pdf",
          topics: ["DST", "1031"],
          fetched_at: "2026-10-28T00:00:00Z",
          updated_at: "2026-10-28T00:00:00Z",
          chunk_count: 2,
          embeddings: [embedding],
        },
      ],
    }),
  );
  await page.route(
    "**/api/proxy/admin/documents/irs-ruling/chunks**",
    (route) =>
      json(route, {
        document_id: "irs-ruling",
        title: "Revenue Ruling 2004-86",
        total: 2,
        offset: 0,
        limit: 10,
        items: [0, 1].map((i) => ({
          id: `chunk-${i}`,
          chunk_index: i,
          content: `Stored source passage ${i + 1}.`,
          page_number: i + 1,
          section: null,
          embedding_model: embedding.model_id,
          dimensions: 1024,
          embedding_preview: [0.25, -0.125, 0, 0.5, 0, 0, 0, 0],
        })),
      }),
  );
  await signIn(page, ADMIN);
  await page.getByRole("link", { name: "Admin dashboard" }).click();
  await expect(
    page.getByRole("heading", { name: "Admin dashboard", exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Knowledge base" }).click();
  await expect(
    page.getByText("Ingested documents", { exact: true }).locator(".."),
  ).toContainText("1");
  await expect(
    page.getByText("Embedded chunks", { exact: true }).locator(".."),
  ).toContainText("2");
  await expect(
    page.getByRole("link", { name: /Original source/ }),
  ).toHaveAttribute("target", "_blank");
  await page.getByRole("button", { name: /View chunks & embeddings/ }).click();
  const panel = page.getByRole("dialog");
  await expect(panel).toContainText("Stored source passage 1.");
  await expect(panel).toContainText("1024");
  await panel
    .getByText("Vector preview (first 8 of 1024 values)")
    .first()
    .click();
  await expect(
    panel
      .getByRole("article", { name: "Chunk 1", exact: true })
      .getByText(/\[0.250000, -0.125000/),
  ).toBeVisible();
  await expect(
    panel.getByRole("button", { name: "Next chunks" }),
  ).toBeDisabled();
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "Knowledge base" }).focus();
  await page.keyboard.press("ArrowLeft");
  await expect(
    page.getByRole("tab", { name: "Question history" }),
  ).toHaveAttribute("aria-selected", "true");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
});

test("knowledge base reports loading failures and supports refresh and an empty corpus", async ({
  page,
}) => {
  await mockApi(page, {
    isAdmin: true,
    admin: (route) =>
      json(route, {
        items: [],
        total: 0,
        limit: 25,
        offset: 0,
        summary: {
          total: 0,
          answered: 0,
          refused: 0,
          unsupported: 0,
          failed: 0,
        },
      }),
  });
  let attempts = 0;
  await page.route("**/api/proxy/admin/knowledge-base", (route) =>
    ++attempts === 1
      ? json(route, { detail: "Service unavailable" }, 503)
      : json(route, {
          document_count: 0,
          chunk_count: 0,
          query_embedding_model: "titan",
          answer_model: "deepseek",
          fallback_answer_model: null,
          embeddings: [],
          documents: [],
        }),
  );
  await signIn(page, ADMIN);
  await page.goto("/admin");
  await page.getByRole("tab", { name: "Knowledge base" }).click();
  await expect(
    page.getByRole("region", { name: "Knowledge base" }).getByRole("alert"),
  ).toContainText("Couldn't load the ingested documents");
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(
    page.getByText("No documents have been ingested yet."),
  ).toBeVisible();
  await expect(
    page.getByText("No embeddings have been stored yet."),
  ).toBeVisible();
});

test("the real proxy allows only the new read-only corpus routes", async ({
  page,
}) => {
  // APIRequestContext bypasses page.route stubs and exercises the Next.js proxy itself.
  // The test server's upstream is deliberately unavailable; an allowed authenticated
  // request reaches it and returns 502, while unknown paths remain 404.
  const paths = [
    "chat/activity/11111111-1111-4111-8111-111111111111",
    "chat/activity/11111111-1111-4111-8111-111111111111/result",
    "admin/knowledge-base",
    "admin/documents/irs-rev-rul-2004-86/chunks",
  ];
  for (const path of paths) {
    const response = await page.request.get(`/api/proxy/${path}`);
    expect(response.status()).toBe(401);
  }
  await mockApi(page, { isAdmin: true });
  await signIn(page, ADMIN);
  for (const path of paths) {
    const response = await page.request.get(`/api/proxy/${path}`);
    expect(response.status()).toBe(502);
    expect(await response.json()).toMatchObject({
      detail: "The assistant service is unavailable. Please try again.",
    });
  }
  expect(
    (await page.request.post("/api/proxy/admin/knowledge-base")).status(),
  ).toBe(404);
  expect(
    (
      await page.request.get("/api/proxy/admin/documents/not/a/source/chunks")
    ).status(),
  ).toBe(404);
});

test("a long answer opens at its explanation rather than the bottom of its sources", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const response = exchange();
  response.citations[0].quotes = [
    "This is a long supporting passage. ".repeat(180),
  ];
  await mockApi(page, { chat: (route) => json(route, response) });
  await signIn(page);
  await page
    .getByLabel(/Ask about/)
    .fill("What is a Delaware Statutory Trust?");
  await page.getByRole("button", { name: "Send question" }).click();
  const answer = page.getByRole("article", { name: "Assistant answer" });
  await expect(answer).toBeVisible();
  const pane = page.getByRole("region", { name: "Conversation messages" });
  await expect.poll(() => pane.evaluate((e) => e.scrollTop)).toBeLessThan(100);
  await expect(
    answer
      .locator("p")
      .filter({ hasText: response.explanation[0].text })
      .first(),
  ).toBeInViewport();
  expect(await pane.evaluate((e) => e.scrollHeight > e.clientHeight)).toBe(
    true,
  );
});

test("live activity comes from the server and remains attached to the answer", async ({
  page,
}) => {
  let finish!: () => void;
  const completed = new Promise<void>((resolve) => {
    finish = resolve;
  });
  let requestId = "";
  const step = {
    id: 0,
    kind: "model_call",
    status: "running",
    started_ms: 50,
    finished_ms: null,
    model_id: "deepseek.v3.2",
    source_count: null,
  };
  await mockApi(page, {
    chat: async (route) => {
      requestId = route.request().postDataJSON().activity_id;
      await completed;
      await json(
        route,
        exchange({
          activity: [{ ...step, status: "completed", finished_ms: 150 }],
        }),
      );
    },
  });
  await page.route("**/api/proxy/chat/activity/*", (route) => {
    expect(route.request().url()).toContain(requestId);
    return json(route, {
      id: requestId,
      steps: [step],
      finished: false,
      exchange_id: null,
    });
  });
  await signIn(page);
  await page.getByLabel(/Ask about/).fill("What is a DST?");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.getByText("Amazon Nova Lite", { exact: true })).toHaveCount(
    0,
  );
  await expect(page.getByText("DeepSeek V3.2", { exact: true })).toBeVisible();
  await expect(
    page.getByText(
      "These are service steps, not the model's private reasoning.",
    ),
  ).toBeVisible();
  finish();
  const answer = page.getByRole("article", { name: "Assistant answer" });
  await expect(answer).toBeVisible();
  await answer.getByText("View activity", { exact: true }).click();
  await expect(
    answer.getByText("DeepSeek V3.2", { exact: true }),
  ).toBeVisible();
  await expect(answer.getByText("0.1s", { exact: true })).toBeVisible();
});

test("a progress outage does not invent completed steps or lose the answer", async ({
  page,
}) => {
  let finish!: () => void;
  const completed = new Promise<void>((resolve) => {
    finish = resolve;
  });
  await mockApi(page, {
    chat: async (route) => {
      await completed;
      await json(route, exchange());
    },
  });
  await page.route("**/api/proxy/chat/activity/*", (route) =>
    json(route, { detail: "Unavailable" }, 503),
  );
  await signIn(page);
  await page.getByLabel(/Ask about/).fill("What is a DST?");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(
    page.getByText(
      "Live activity is temporarily unavailable. Your question is still being processed.",
    ),
  ).toBeVisible();
  await expect(
    page.getByText("Checking supporting quotes", { exact: true }),
  ).toHaveCount(0);
  finish();
  await expect(
    page.getByRole("article", { name: "Assistant answer" }),
  ).toBeVisible();
});

test("network retries reuse the request ID instead of creating a second answer", async ({
  page,
}) => {
  const ids: string[] = [];
  await mockApi(page, {
    chat: (route) => {
      ids.push(route.request().postDataJSON().activity_id);
      return ids.length === 1 ? route.abort("failed") : json(route, exchange());
    },
  });
  await signIn(page);
  await page.getByLabel(/Ask about/).fill("What is a DST?");
  await page.getByRole("button", { name: "Send question" }).click();
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(
    page.getByRole("article", { name: "Assistant answer" }),
  ).toBeVisible();
  expect(ids).toHaveLength(2);
  expect(ids[0]).toBeTruthy();
  expect(ids[0]).toBe(ids[1]);
});

test("the app disables framing and unnecessary browser capabilities", async ({
  page,
}) => {
  const response = await page.request.get("/login");
  expect(response.headers()["x-frame-options"]).toBe("DENY");
  expect(response.headers()["x-content-type-options"]).toBe("nosniff");
  expect(response.headers()["permissions-policy"]).toContain("camera=()");
  expect(response.headers()["content-security-policy"]).toContain(
    "frame-ancestors 'none'",
  );
});

test("accepted background work keeps reporting activity until its checked result is ready", async ({
  page,
}) => {
  let ready = false;
  let nonce = "";
  const step = {
    id: 0,
    kind: "model_call",
    status: "running",
    started_ms: 0,
    finished_ms: null,
    model_id: "deepseek.v3.2",
    source_count: null,
  };
  await mockApi(page, {
    chat: (route) => {
      const body = route.request().postDataJSON();
      expect(body.background).toBe(true);
      nonce = body.activity_id;
      return json(
        route,
        { id: nonce, steps: [], finished: false, exchange_id: null },
        202,
      );
    },
  });
  await page.route("**/api/proxy/chat/activity/*", (route) =>
    json(route, {
      id: nonce,
      steps: [step],
      finished: ready,
      exchange_id: null,
    }),
  );
  await page.route("**/api/proxy/chat/activity/*/result", (route) =>
    ready
      ? json(
          route,
          exchange({
            activity: [{ ...step, status: "completed", finished_ms: 1200 }],
          }),
        )
      : json(route, { detail: "Still processing" }, 409),
  );
  await signIn(page);
  await page.getByLabel(/Ask about/).fill("Explain a 1031 exchange");
  const accepted = page.waitForResponse(
    (r) => r.url().includes("/api/proxy/chat") && r.status() === 202,
  );
  await page.getByRole("button", { name: "Send question" }).click();
  await accepted;
  await expect(page.getByText("DeepSeek V3.2", { exact: true })).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(0);
  ready = true;
  await expect(
    page.getByRole("article", { name: "Assistant answer" }),
  ).toBeVisible();
});

test("an expired background request uses a fresh ID only after an explicit retry", async ({
  page,
}) => {
  const ids: string[] = [];
  await mockApi(page, {
    chat: (route) => {
      ids.push(route.request().postDataJSON().activity_id);
      return ids.length === 1
        ? json(
            route,
            { id: ids[0], steps: [], finished: true, exchange_id: null },
            202,
          )
        : json(route, exchange());
    },
  });
  await page.route("**/api/proxy/chat/activity/*/result", (route) =>
    json(route, { detail: "This request expired." }, 410),
  );
  await signIn(page);
  await page.getByLabel(/Ask about/).fill("What is a DST?");
  await page.getByRole("button", { name: "Send question" }).click();
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.getByRole("article")).toHaveCount(1);
  expect(ids).toHaveLength(2);
  expect(ids[0]).not.toBe(ids[1]);
});
