import { BookOpenCheck, FileSearch, ShieldCheck } from "lucide-react";
import type { Metadata } from "next";

import { Wordmark } from "@/components/brand";
import { LoginForm } from "@/features/auth/login-form";

export const metadata: Metadata = { title: "Sign in · Sightbridge Advisor Intelligence" };

const POINTS = [
  { icon: FileSearch, text: "Answers drawn only from 13 public sources: IRS, SEC, FINRA and more" },
  { icon: BookOpenCheck, text: "Every answer cites the exact passage it relies on" },
  { icon: ShieldCheck, text: "Educational only, never personalised tax, legal or investment advice" },
];

export default function LoginPage() {
  return (
    <main className="grid min-h-dvh lg:grid-cols-[1.05fr_1fr]">
      <section className="bg-primary text-primary-foreground hidden flex-col justify-between p-12 lg:flex">
        <Wordmark inverted />
        <div className="max-w-md space-y-8">
          <div className="space-y-4">
            <p className="text-sand text-sm font-medium tracking-[0.18em] uppercase">
              For financial advisors
            </p>
            <h1 className="text-4xl leading-tight font-semibold">
              Trusted answers for tax-advantaged real estate
            </h1>
            <p className="text-lg leading-relaxed text-white/75">
              Clear, sourced explanations of 1031 exchanges, Delaware Statutory Trusts and 721
              exchanges into REIT operating partnerships.
            </p>
          </div>
          <ul className="space-y-4">
            {POINTS.map(({ icon: Icon, text }) => (
              <li key={text} className="flex gap-3 text-white/85">
                <Icon className="text-sand mt-0.5 size-5 shrink-0" aria-hidden />
                <span>{text}</span>
              </li>
            ))}
          </ul>
        </div>
        <p className="text-xs text-white/50">
          Prototype built for an assessment. Not an official Sightbridge product.
        </p>
      </section>

      <section className="flex items-center justify-center px-4 py-10 sm:px-8">
        <div className="w-full max-w-sm space-y-8">
          <div className="space-y-3 lg:hidden">
            <Wordmark />
            <p className="text-muted-foreground text-sm">
              Trusted answers for tax-advantaged real estate
            </p>
          </div>
          <div className="space-y-1.5">
            <h2 className="text-2xl font-semibold">Sign in</h2>
            <p className="text-muted-foreground text-sm">
              Use the advisor or reviewer account you were given.
            </p>
          </div>
          <LoginForm />
          <p className="text-muted-foreground text-xs leading-relaxed">
            Educational information only, not tax, legal or investment advice.
          </p>
        </div>
      </section>
    </main>
  );
}
