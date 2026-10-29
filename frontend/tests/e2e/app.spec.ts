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
  await expect(page.getByRole("alert").filter({ hasText: "Incorrect email or password." })).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
});

test("a suggested question is submitted and the cited answer is shown", async ({ page }) => {
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
  await expect(answer).toContainText("recognized as an entity separate from its owners");
  expect(sent).toEqual({ question: "What is a Delaware Statutory Trust?", conversation_id: null });

  const source = answer.getByRole("link", { name: /Revenue Ruling 2004-86/ });
  await expect(source).toHaveAttribute("href", "https://home.treasury.gov/rr0486.pdf#page=2");
  await expect(source).toHaveAttribute("target", "_blank");
  await expect(answer.getByRole("link", { name: "Source 1" }).first()).toHaveAttribute(
    "href",
    /#cite-.+-1$/,
  );
  await expect(answer).toContainText("Educational information only");
  await expect(page).toHaveURL(/\?c=11111111-1111-4111-8111-111111111111/);
});

test("refusals show the outcome, the reason and the disclaimer", async ({ page }) => {
  await mockApi(page, {
    chat: (route) =>
      json(
        route,
        exchange({
          question: "Should I invest my $500,000 in a DST?",
          status: "refused",
          reason: "personalized_advice",
          notice: "I can't make recommendations or assess a specific person's situation.",
          explanation: [],
          key_considerations: [],
          citations: [],
        }),
      ),
  });
  await signIn(page);
  await page.getByLabel(/Ask about/).fill("Should I invest my $500,000 in a DST?");
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
        await json(route, { detail: "Too many questions in a short period. Please wait and try again." }, 429);
      } else {
        await json(route, exchange());
      }
    },
  });
  await signIn(page);
  await page.getByLabel(/Ask about/).fill("How does a 1031 exchange work?");
  await page.keyboard.press("Enter");

  await expect(page.getByRole("alert").filter({ hasText: "Too many questions" })).toBeVisible();
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("article", { name: "Assistant answer" })).toBeVisible();
});

test("advisors get no audit navigation and are blocked from the audit page", async ({ page }) => {
  await mockApi(page);
  await signIn(page);
  await expect(page.getByRole("navigation", { name: "Main" })).not.toContainText("Audit log");

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
        items: [{ ...exchange(), user_id: "u1", user_email: "advisor@e2e.test", answer_text: "…", model_id: "m", input_tokens: 1, output_tokens: 1, latency_ms: 900, retrieved_passages: 6, dropped_claims: 0, request_id: "r1" }],
        total: 1,
        limit: 25,
        offset: 0,
        summary: { total: 7, answered: 4, refused: 2, unsupported: 1, failed: 0 },
      });
    },
  });
  await signIn(page, ADMIN);
  await page.getByRole("link", { name: "Audit log" }).click();

  await expect(page.getByRole("table")).toContainText("advisor@e2e.test");
  await expect(page.getByText("Total exchanges").locator("..")).toContainText("7");

  await page.getByLabel("Outcome").click();
  const trigger = await page.getByLabel("Outcome", { exact: true }).boundingBox();
  const menu = await page.getByRole("listbox").boundingBox();
  expect(trigger).not.toBeNull();
  expect(menu).not.toBeNull();
  expect(menu!.y >= trigger!.y + trigger!.height || menu!.y + menu!.height <= trigger!.y).toBe(true);
  expect(menu!.x + menu!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  await page.getByRole("option", { name: "Refused" }).click();
  await expect.poll(() => requested.at(-1)).toContain("status=refused");

  const question = page.getByRole("button", { name: "What is a Delaware Statutory Trust?" });
  await expect(question.locator("svg")).toBeVisible();
  await expect(question).toHaveAttribute("aria-haspopup", "dialog");
  await question.click();
  await expect(page.getByText("Answer shown to the user")).toBeVisible();
});

test("capabilities answers show help without a refusal or fabricated sources", async ({ page }) => {
  await mockApi(page, {
    chat: (route) => json(route, exchange({
      question: "What kind of information you can give to me",
      reason: "capabilities",
      notice: "I can help you understand 1031 exchanges, DSTs, and 721 exchanges. For example: How does a 1031 exchange work?",
      explanation: [], key_considerations: [], citations: [],
    })),
  });
  await signIn(page);
  await page.getByLabel(/Ask about/).fill("What kind of information you can give to me");
  await page.getByRole("button", { name: "Send question" }).click();
  const answer = page.getByRole("article", { name: "Assistant answer" });
  await expect(answer).toContainText("I can help you understand");
  await expect(answer).toContainText("Educational information only");
  await expect(answer.getByText("Refused", { exact: true })).toHaveCount(0);
  await expect(answer.getByRole("heading", { name: "Sources" })).toHaveCount(0);
});

test("long conversations scroll inside the message panel with the composer visible", async ({ page }) => {
  await mockApi(page);
  const id = "11111111-1111-4111-8111-111111111111";
  await page.route(`**/api/proxy/conversations/${id}`, (route) => json(route, {
    id, title: "Long conversation", exchanges: Array.from({ length: 12 }, () => exchange()),
  }));
  await signIn(page);
  await page.goto(`/?c=${id}`);
  const messages = page.getByRole("region", { name: "Conversation messages" });
  await expect(messages.getByRole("article")).toHaveCount(12);
  await messages.evaluate((element) => { element.scrollTop = 0; });
  const rect = await messages.boundingBox();
  await page.mouse.move(rect!.x + rect!.width / 2, rect!.y + 40);
  await page.mouse.wheel(0, 500);
  await expect.poll(() => messages.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  const metrics = await page.evaluate(() => ({
    scrollTop: window.scrollY,
    documentHeight: document.documentElement.scrollHeight,
    viewportHeight: window.innerHeight,
  }));
  expect(metrics.scrollTop).toBe(0);
  expect(metrics.documentHeight).toBeLessThanOrEqual(metrics.viewportHeight + 1);
  const composer = await page.getByLabel(/Ask about/).boundingBox();
  expect(composer!.y).toBeGreaterThan(0);
  expect(composer!.y + composer!.height).toBeLessThanOrEqual(metrics.viewportHeight);
});

test("the chat layout never scrolls horizontally", async ({ page }) => {
  await mockApi(page);
  await signIn(page);
  await page.getByLabel(/Ask about/).fill("What is a DST?");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("article", { name: "Assistant answer" })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});
