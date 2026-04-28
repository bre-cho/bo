export default function StatusBadge({ status }: { status: string }) {
  const label =
    status === "ok" ? "Tot" :
    status === "configured" ? "Da cau hinh" :
    status === "degraded" ? "Suy giam" :
    status === "error" ? "Loi" :
    status === "missing" ? "Thieu" :
    status === "unknown" ? "Khong ro" :
    status;

  const cls =
    status === "ok"       ? "badge-ok" :
    status === "degraded" ? "badge-degraded" :
    status === "error"    ? "badge-error" :
    "badge-missing";
  return <span className={cls}>{label}</span>;
}
