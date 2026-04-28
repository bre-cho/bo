"use client";
import { useState, useTransition } from "react";

interface Props {
  label:     string;
  onConfirm: () => Promise<unknown>;
  variant?:  "primary" | "danger";
  disabled?: boolean;
}

export default function ActionButton({ label, onConfirm, variant = "primary", disabled }: Props) {
  const [pending, startTransition] = useTransition();
  const [msg, setMsg] = useState<string | null>(null);

  function handleClick() {
    setMsg(null);
    startTransition(async () => {
      try {
        await onConfirm();
        setMsg("✓ Thanh cong");
      } catch (e: unknown) {
        setMsg(`✗ Loi: ${(e as Error).message}`);
      }
    });
  }

  return (
    <div className="flex items-center gap-3">
      <button
        className={variant === "danger" ? "btn-danger" : "btn-primary"}
        onClick={handleClick}
        disabled={disabled || pending}
      >
        {pending ? "…" : label}
      </button>
      {msg && (
        <span className={`text-xs ${msg.startsWith("✓") ? "text-green-400" : "text-red-400"}`}>
          {msg}
        </span>
      )}
    </div>
  );
}
