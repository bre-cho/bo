"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_LINKS = [
  { href: "/dashboard",  label: "Tong quan" },
  { href: "/controls",   label: "Dieu khien" },
  { href: "/evolution",  label: "Tien hoa" },
  { href: "/logs",       label: "Nhat ky lenh" },
  { href: "/audit",      label: "Kiem toan" },
];

export default function NavBar() {
  const path = usePathname();
  return (
    <nav className="border-b" style={{ borderColor: "var(--border)", background: "var(--surface)" }}>
      <div className="max-w-7xl mx-auto px-4 flex items-center gap-6 h-14">
        <span className="font-bold text-blue-400 text-lg tracking-tight">Robot BO</span>
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
