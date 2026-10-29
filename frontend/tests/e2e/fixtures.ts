import type { Page, Route } from "@playwright/test";

export const ADVISOR = { email: "advisor@e2e.test", password: "advisor-e2e-pass" };
export const ADMIN = { email: "admin@e2e.test", password: "admin-e2e-pass" };
const DISCLAIMER = "Educational information only, not tax, legal or investment advice.";
const CONVERSATION = "11111111-1111-4111-8111-111111111111";

export function exchange(overrides: Record<string, unknown> = {}) {
  return {
    id: crypto.randomUUID(),
    conversation_id: CONVERSATION,
    question: "What is a Delaware Statutory Trust?",
    status: "answered",
    reason: null,
    notice: null,
    explanation: [
      { text: "A Delaware statutory trust is recognized as an entity separate from its owners.", citations: [1] },
    ],
    key_considerations: [{ text: "DST interests can be treated as real property for § 1031.", citations: [1] }],
    citations: [
      {
        number: 1,
        document_id: "irs-rev-rul-2004-86",
        title: "Revenue Ruling 2004-86",
        publisher: "Internal Revenue Service",
        url: "https://home.treasury.gov/rr0486.pdf#page=2",
        source_url: "https://home.treasury.gov/rr0486.pdf",
        location: "p. 2",
        quotes: ["recognized as an entity separate from its owners"],
      },
    ],
    disclaimer: DISCLAIMER,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

const json = (route: Route, body: unknown, status = 200, headers = {}) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body), headers });

/** Default backend stubs; individual tests override `chat` or `admin` handlers. */
export async function mockApi(
  page: Page,
  opts: {
    isAdmin?: boolean;
    chat?: (route: Route) => Promise<void>;
    admin?: (route: Route) => Promise<void>;
  } = {},
) {
  await page.route("**/api/proxy/me", (r) =>
    json(r, { user_id: "u1", email: opts.isAdmin ? ADMIN.email : ADVISOR.email, is_admin: !!opts.isAdmin }),
  );
  await page.route("**/api/proxy/conversations", (r) => json(r, []));
  await page.route("**/api/proxy/chat", opts.chat ?? ((r) => json(r, exchange())));
  await page.route(
    "**/api/proxy/admin/exchanges**",
    opts.admin ?? ((r) => json(r, { detail: "Admin access required." }, 403)),
  );
}

export { json };

export async function signIn(page: Page, user = ADVISOR) {
  await page.goto("/login");
  await page.getByLabel("Work email").fill(user.email);
  await page.getByLabel("Password").fill(user.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("/");
}
