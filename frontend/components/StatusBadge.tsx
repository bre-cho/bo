export default function StatusBadge({ status }: { status: string }) {
  const cls =
    status === "ok"       ? "badge-ok" :
    status === "degraded" ? "badge-degraded" :
    status === "error"    ? "badge-error" :
    "badge-missing";
  return <span className={cls}>{status}</span>;
}
