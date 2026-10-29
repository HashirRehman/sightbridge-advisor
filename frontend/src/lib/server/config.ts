import "server-only";

import { z } from "zod";

const schema = z
  .object({
    APP_ENV: z.enum(["local", "production"]).default("local"),
    API_BASE_URL: z.url(),
    AUTH_PROVIDER: z.enum(["cognito", "local"]).default("cognito"),
    COGNITO_REGION: z.string().default("us-east-1"),
    COGNITO_APP_CLIENT_ID: z.string().default(""),
    LOCAL_AUTH_SECRET: z.string().default(""),
    // JSON array of {email, password, role}; local development only.
    LOCAL_DEMO_USERS: z.string().default("[]"),
  })
  .superRefine((env, ctx) => {
    if (env.AUTH_PROVIDER === "cognito" && !env.COGNITO_APP_CLIENT_ID) {
      ctx.addIssue({ code: "custom", message: "COGNITO_APP_CLIENT_ID is required" });
    }
    if (env.AUTH_PROVIDER === "local" && env.LOCAL_AUTH_SECRET.length < 32) {
      ctx.addIssue({ code: "custom", message: "LOCAL_AUTH_SECRET must be >= 32 chars" });
    }
    if (env.APP_ENV === "production" && env.AUTH_PROVIDER !== "cognito") {
      ctx.addIssue({ code: "custom", message: "production requires Cognito auth" });
    }
  });

export type ServerConfig = z.infer<typeof schema>;

let cached: ServerConfig | undefined;

export function serverConfig(): ServerConfig {
  cached ??= schema.parse(process.env);
  return cached;
}
