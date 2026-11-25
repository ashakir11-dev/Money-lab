"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { Mark } from "@/components/Mark";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";

const LINKS = [
  { href: "/", label: "Home", id: "nav-home-page", exact: true },
  { href: "/data", label: "Data", id: "nav-data" },
  { href: "/builder", label: "Builder", id: "nav-builder" },
  { href: "/report", label: "Reports", id: "nav-report" },
  { href: "/compare", label: "Compare", id: "nav-compare" },
  { href: "/paper", label: "Paper", id: "nav-paper" },
  { href: "/gates", label: "Gates", id: "nav-gates" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const path = usePathname() || "/";
  const health = useAsync(() => api.health(), []);
  const active = (href: string, exact?: boolean) => {
    if (exact) return path === "/";
    if (href === "/report") return path.startsWith("/report") || path.startsWith("/run");
    return path === href || path.startsWith(`${href}/`);
  };

  const lamp = health.error ? "off" : health.data ? "on" : "busy";
  const lampTitle = health.error
    ? "Research API offline"
    : health.data
      ? `Engine ${health.data.engine_version}`
      : "Connecting";

  return (
    <>
      <header className="shell-top">
        <nav className="topnav" aria-label="Main">
          <Link href="/" className="brand-block" data-testid="nav-home">
            <Mark size={28} />
            <span className="brand-name">Money-lab</span>
          </Link>
          <div className="navlinks">
            {LINKS.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                data-testid={l.id}
                className={active(l.href, l.exact) ? "active" : undefined}
              >
                {l.label}
              </Link>
            ))}
          </div>
          <span className={`lamp lamp-${lamp}`} title={lampTitle} aria-label={lampTitle} />
        </nav>
      </header>
      <main className="container">{children}</main>
    </>
  );
}
