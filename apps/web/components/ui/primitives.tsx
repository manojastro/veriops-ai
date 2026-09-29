"use client";

// shadcn/ui-style primitives, hand-written to keep the dependency surface small.

import type { ButtonHTMLAttributes, HTMLAttributes, InputHTMLAttributes, ReactNode, TextareaHTMLAttributes } from "react";

import { cn } from "@/lib/format";

type ButtonVariant = "default" | "secondary" | "outline" | "ghost" | "success" | "danger";

const buttonVariants: Record<ButtonVariant, string> = {
  default: "bg-slate-900 text-white hover:bg-slate-800",
  secondary: "bg-slate-100 text-slate-900 hover:bg-slate-200",
  outline: "border border-slate-300 bg-white text-slate-800 hover:bg-slate-50",
  ghost: "text-slate-700 hover:bg-slate-100",
  success: "bg-emerald-700 text-white hover:bg-emerald-800",
  danger: "bg-rose-700 text-white hover:bg-rose-800",
};

export function Button({
  className,
  variant = "default",
  size = "md",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; size?: "sm" | "md" }) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 disabled:pointer-events-none disabled:opacity-50",
        size === "sm" ? "h-8 px-3 text-xs" : "h-9 px-4 text-sm",
        buttonVariants[variant],
        className,
      )}
      {...props}
    />
  );
}

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-lg border border-slate-200 bg-white shadow-sm", className)} {...props} />;
}

export function CardHeader({
  title,
  description,
  action,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-start justify-between gap-4 border-b border-slate-100 px-4 py-3", className)}>
      <div>
        <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
        {description && <p className="mt-0.5 text-xs text-slate-500">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function CardBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-4", className)} {...props} />;
}

type Tone = "neutral" | "success" | "warning" | "danger" | "info";

const tones: Record<Tone, string> = {
  neutral: "bg-slate-100 text-slate-700 ring-slate-200",
  success: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  warning: "bg-amber-50 text-amber-800 ring-amber-200",
  danger: "bg-rose-50 text-rose-800 ring-rose-200",
  info: "bg-sky-50 text-sky-800 ring-sky-200",
};

export function Badge({ tone = "neutral", className, children }: { tone?: Tone; className?: string; children: ReactNode }) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset", tones[tone], className)}>
      {children}
    </span>
  );
}

export function statusTone(status: string | null | undefined): Tone {
  switch (status) {
    case "RESOLVED":
    case "ACCEPTED":
    case "APPROVED":
    case "SUPPORTED":
    case "success":
    case "LOW":
      return "success";
    case "AWAITING_APPROVAL":
    case "MEDIUM":
    case "ROLLED_BACK":
    case "PENDING":
    case "warning":
    case "INCONCLUSIVE":
      return "warning";
    case "FAILED":
    case "REJECTED":
    case "REFUTED":
    case "ESCALATED":
    case "REMEDIATION_REJECTED":
    case "HIGH":
    case "CRITICAL":
    case "danger":
      return "danger";
    case "INVESTIGATING":
    case "REMEDIATING":
    case "VERIFYING":
    case "info":
      return "info";
    default:
      return "neutral";
  }
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  return <Badge tone={statusTone(status)}>{(status ?? "—").replace(/_/g, " ")}</Badge>;
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-9 w-full rounded-md border border-slate-300 bg-white px-3 text-sm placeholder:text-slate-400",
        "focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500",
        className,
      )}
      {...props}
    />
  );
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm placeholder:text-slate-400",
        "focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500",
        className,
      )}
      {...props}
    />
  );
}

export function Table({ className, ...props }: HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="overflow-x-auto">
      <table className={cn("w-full text-left text-sm", className)} {...props} />
    </div>
  );
}

export function Th({ className, ...props }: HTMLAttributes<HTMLTableCellElement>) {
  return <th className={cn("border-b border-slate-200 px-3 py-2 text-xs font-medium uppercase tracking-wide text-slate-500", className)} {...props} />;
}

export function Td({ className, ...props }: HTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("border-b border-slate-100 px-3 py-2 align-top", className)} {...props} />;
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone }) {
  return (
    <Card className="px-4 py-3">
      <div className="text-xs font-medium text-slate-500">{label}</div>
      <div
        className={cn(
          "tabular mt-1 text-2xl font-semibold",
          tone === "danger" && "text-rose-700",
          tone === "success" && "text-emerald-700",
          tone === "warning" && "text-amber-700",
        )}
      >
        {value}
      </div>
      {hint && <div className="mt-0.5 text-xs text-slate-500">{hint}</div>}
    </Card>
  );
}

export function Bar({ value, max = 100, tone = "neutral" }: { value: number; max?: number; tone?: Tone }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const color = { neutral: "bg-slate-500", success: "bg-emerald-600", warning: "bg-amber-500", danger: "bg-rose-600", info: "bg-sky-600" }[tone];
  return (
    <div className="h-1.5 w-full rounded-full bg-slate-100">
      <div className={cn("h-1.5 rounded-full", color)} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-md border border-dashed border-slate-200 px-4 py-6 text-center text-sm text-slate-500">{children}</div>;
}

export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: Array<{ id: T; label: string; count?: number }>;
  active: T;
  onChange: (id: T) => void;
}) {
  return (
    <div className="flex gap-1 border-b border-slate-200" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={active === t.id}
          onClick={() => onChange(t.id)}
          className={cn(
            "-mb-px border-b-2 px-3 py-2 text-sm font-medium",
            active === t.id ? "border-slate-900 text-slate-900" : "border-transparent text-slate-500 hover:text-slate-800",
          )}
        >
          {t.label}
          {t.count !== undefined && <span className="ml-1.5 rounded bg-slate-100 px-1.5 text-[11px] text-slate-600">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}
