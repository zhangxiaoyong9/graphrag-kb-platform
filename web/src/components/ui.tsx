/** Reusable presentational primitives built on the design-token classes. */
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "../lib/cn";
import type { Tone } from "../lib/status";
import { toneClasses } from "../lib/status";
import { money } from "../lib/format";

/* ------------------------------------------------------------------ Card */

export function Card({
  className,
  children,
  pad = true,
}: {
  className?: string;
  children: ReactNode;
  pad?: boolean;
}) {
  return <div className={cn("card", pad && "card-pad", className)}>{children}</div>;
}

export function CardHeader({
  title,
  subtitle,
  icon,
  actions,
  className,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-start justify-between gap-3", className)}>
      <div className="flex items-start gap-3 min-w-0">
        {icon && (
          <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-grad-soft text-brand">
            {icon}
          </span>
        )}
        <div className="min-w-0">
          <h3 className="text-[15px] font-semibold leading-tight">{title}</h3>
          {subtitle && <p className="mt-0.5 text-[13px] text-muted truncate">{subtitle}</p>}
        </div>
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

/* ---------------------------------------------------------------- Button */

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger" | "success";
  size?: "sm" | "md";
};

export function Button({
  variant = "secondary",
  size = "md",
  className,
  children,
  ...rest
}: ButtonProps) {
  const variantCls = {
    primary: "btn-primary",
    secondary: "btn-secondary",
    ghost: "btn-ghost",
    danger: "btn-danger",
    success: "btn-success",
  }[variant];
  return (
    <button
      className={cn("btn", variantCls, size === "sm" ? "btn-sm" : "btn-md", className)}
      {...rest}
    >
      {children}
    </button>
  );
}

/* ----------------------------------------------------------------- Badge */

export function Badge({
  tone = "neutral",
  children,
  className,
  dot = false,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
  dot?: boolean;
}) {
  return (
    <span className={cn("badge", toneClasses(tone), className)}>
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" />}
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ Stat */

export function Stat({
  label,
  value,
  sub,
  icon,
  accent = false,
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  icon?: ReactNode;
  accent?: boolean;
}) {
  return (
    <div
      className={cn(
        "card relative overflow-hidden p-4",
        accent && "bg-brand-grad text-white border-transparent",
      )}
    >
      {accent && <div className="pointer-events-none absolute -right-6 -top-8 h-24 w-24 rounded-full bg-white/15 blur-xl" />}
      <div className="flex items-center justify-between">
        <span className={cn("text-[13px]", accent ? "text-white/85" : "text-muted")}>{label}</span>
        {icon && <span className={cn(accent ? "text-white/90" : "text-brand")}>{icon}</span>}
      </div>
      <div className={cn("nums mt-2 text-[26px] font-semibold leading-none", accent ? "text-white" : "text-ink")}>
        {value}
      </div>
      {sub && <div className={cn("mt-1.5 text-xs", accent ? "text-white/80" : "text-muted")}>{sub}</div>}
    </div>
  );
}

/* ------------------------------------------------------------- EmptyState */

export function EmptyState({
  icon,
  title,
  hint,
  action,
}: {
  icon?: ReactNode;
  title: ReactNode;
  hint?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-line-strong px-6 py-10 text-center">
      {icon && <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-surface-2 text-muted">{icon}</div>}
      <p className="text-sm font-medium text-ink">{title}</p>
      {hint && <p className="mt-1 max-w-sm text-[13px] text-muted">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/* -------------------------------------------------------------- Skeleton */

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton h-4 w-full", className)} />;
}

/* --------------------------------------------------------------- Spinner */

export function Spinner({ className }: { className?: string }) {
  return (
    <svg
      className={cn("animate-spin", className)}
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" opacity="0.2" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

/* ----------------------------------------------------------------- Field */

export function Field({
  label,
  hint,
  htmlFor,
  children,
}: {
  label: ReactNode;
  hint?: ReactNode;
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <label htmlFor={htmlFor} className="block">
      <span className="mb-1.5 flex items-center gap-2 text-[13px] font-medium text-body">
        {label}
        {hint && <span className="text-xs font-normal text-muted">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

/* ------------------------------------------------------------ ProgressBar */

export function ProgressBar({
  value,
  tone = "brand",
  className,
}: {
  value: number; // 0..100
  tone?: Tone;
  className?: string;
}) {
  const barTone: Record<Tone, string> = {
    brand: "bg-brand",
    success: "bg-success",
    danger: "bg-danger",
    warning: "bg-warning",
    info: "bg-info",
    neutral: "bg-neutral",
  };
  return (
    <div className={cn("h-2 w-full overflow-hidden rounded-full bg-surface-2", className)}>
      <div
        className={cn("h-full rounded-full transition-all duration-500", barTone[tone])}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ Money */

export function Money({
  usd,
  className,
}: {
  usd: number | null | undefined;
  className?: string;
}) {
  return <span className={cn("nums", className)}>{money(usd)}</span>;
}

/* ------------------------------------------------------------- PageHeader */

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-[13px] text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
