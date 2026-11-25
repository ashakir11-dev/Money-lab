"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";

const LINKS = [
  { href: "/", label: "Runs", id: "nav-runs" },
  { href: "/builder", label: "Builder", id: "nav-builder" },
  { href: "/compare", label: "Compare", id: "nav-compare" },
  { href: "/data", label: "Data", id: "nav-data" },
];

export function Nav() {
  const path = usePathname() || "/";
  const health = useAsync(() => api.health(), []);
  const active = (href: string) =>
    href === "/" ? path === "/" || path.startsWith("/run") : path.startsWith(href);
  return (
    <nav className="topnav">
      <Link href="/" className="brand">
        Money-lab
      </Link>
      <div className="navlinks">
        {LINKS.map((l) => (
          <Link key={l.href} href={l.href} data-testid={l.id} className={active(l.href) ? "active" : undefined}>
            {l.label}
          </Link>
        ))}
      </div>
      <span className="health" title="Research API status">
        {health.error ? (
          <span className="dot dot-bad">server offline</span>
        ) : health.data ? (
          <span className="dot dot-ok">{health.data.engine_version}</span>
        ) : (
          <span className="dot">connecting…</span>
        )}
      </span>
    </nav>
  );
}
