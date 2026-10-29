"use client";

import { LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { Wordmark } from "@/components/brand";
import { useSession } from "@/components/session-context";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function AppHeader({ leading }: { leading?: React.ReactNode }) {
  const { me } = useSession();
  const pathname = usePathname();
  const [signingOut, setSigningOut] = useState(false);

  const links = [
    { href: "/", label: "Assistant" },
    ...(me?.is_admin ? [{ href: "/admin", label: "Audit log" }] : []),
  ];

  async function signOut() {
    setSigningOut(true);
    await fetch("/api/auth/logout", { method: "POST" }).catch(() => undefined);
    window.location.assign("/login");
  }

  return (
    <header className="bg-card sticky top-0 z-30 flex h-14 shrink-0 items-center gap-3 border-b px-4 sm:px-6">
      {leading}
      <Link href="/" className="rounded-sm" aria-label="Sightbridge Advisor Intelligence home">
        <Wordmark className="[&>span:last-child]:hidden sm:[&>span:last-child]:inline" />
      </Link>
      <nav aria-label="Main" className="ml-2 flex items-center gap-1 sm:ml-6">
        {links.map((link) => {
          const active = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
          return (
            <Link
              key={link.href}
              href={link.href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors",
                active
                  ? "bg-secondary text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {link.label}
            </Link>
          );
        })}
      </nav>
      <div className="ml-auto flex items-center gap-3">
        {me && (
          <span className="text-muted-foreground hidden max-w-[16rem] truncate text-sm md:inline">
            {me.email}
            {me.is_admin && (
              <span className="bg-sand text-foreground ml-2 rounded px-1.5 py-0.5 text-xs font-medium">
                Admin
              </span>
            )}
          </span>
        )}
        <Button variant="ghost" size="sm" onClick={signOut} disabled={signingOut}>
          <LogOut aria-hidden />
          <span className="hidden sm:inline">Sign out</span>
          <span className="sr-only sm:hidden">Sign out</span>
        </Button>
      </div>
    </header>
  );
}
