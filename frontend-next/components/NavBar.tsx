"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_LINKS = [
  { href: "/dashboard",  label: "Dashboard" },
  { href: "/controls",   label: "Controls" },
  { href: "/evolution",  label: "Evolution" },
  { href: "/logs",       label: "Trade Logs" },
  { href: "/audit",      label: "Audit" },
];

export default function NavBar() {
  const path = usePathname();
  return (
    <nav className="border-b" style={{ borderColor: "var(--border)", background: "var(--surface)" }}>
      <div className="max-w-7xl mx-auto px-4 flex items-center gap-6 h-14">
        <span className="font-bold text-blue-400 text-lg tracking-tight">BO Robot</span>
        {NAV_LINKS.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className={`text-sm transition-colors ${
              path.startsWith(l.href)
                ? "text-blue-400 font-semibold"
                : "text-gray-400 hover:text-gray-100"
            }`}
          >
            {l.label}
          </Link>
        ))}
      </div>
    </nav>
  );
}
