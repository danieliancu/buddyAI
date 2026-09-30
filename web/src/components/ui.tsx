import {
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { AlertTriangle, Loader2, X } from "lucide-react";

export function cx(...c: (string | false | null | undefined)[]): string {
  return c.filter(Boolean).join(" ");
}

// ---------- buttons ----------

type Variant = "primary" | "secondary" | "ghost" | "danger";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-accent-fg hover:brightness-110 border-transparent",
  secondary: "bg-surface-2 text-fg hover:bg-border/60 border-border",
  ghost: "bg-transparent text-fg hover:bg-surface-2 border-transparent",
  danger: "bg-danger-bg text-danger hover:bg-danger hover:text-white border-danger/30",
};

export function buttonCls(variant: Variant = "secondary", size: "sm" | "md" = "md", className?: string): string {
  return cx(
    "inline-flex items-center justify-center gap-2 rounded-lg border font-medium transition",
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
    "disabled:cursor-not-allowed disabled:opacity-50",
    size === "sm" ? "h-8 px-2.5 text-xs" : "h-10 px-4 text-sm",
    VARIANTS[variant],
    className,
  );
}

export function Button({
  variant = "secondary",
  size = "md",
  loading,
  icon,
  className,
  children,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: "sm" | "md";
  loading?: boolean;
  icon?: ReactNode;
}) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || loading}
      className={buttonCls(variant, size, className)}
    >
      {loading ? <Loader2 className="size-4 animate-spin" /> : icon}
      {children}
    </button>
  );
}

// ---------- layout ----------

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{title}</h1>
        {subtitle && <div className="mt-1 text-sm text-muted">{subtitle}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({
  title,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={cx("min-w-0 rounded-xl border border-border bg-surface", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
          {title && <h2 className="text-sm font-semibold">{title}</h2>}
          {actions}
        </header>
      )}
      <div className={cx("p-4", bodyClassName)}>{children}</div>
    </section>
  );
}

// ---------- form ----------

export function Field({
  label,
  hint,
  error,
  children,
  className,
  htmlFor,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: string;
  children: ReactNode;
  className?: string;
  htmlFor?: string;
}) {
  return (
    <div className={cx("flex flex-col gap-1.5", className)}>
      <label htmlFor={htmlFor} className="text-xs font-medium text-muted">
        {label}
      </label>
      {children}
      {error ? <p className="text-xs text-danger">{error}</p> : hint ? <p className="text-xs text-muted">{hint}</p> : null}
    </div>
  );
}

const inputCls =
  "w-full rounded-lg border border-border bg-bg px-3 text-sm text-fg placeholder:text-muted/70 " +
  "focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30 disabled:opacity-60";

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...rest} className={cx(inputCls, "h-10", className)} />;
}

export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...rest} className={cx(inputCls, "min-h-24 py-2 leading-relaxed", className)} />;
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...rest} className={cx(inputCls, "h-10 pr-8", className)}>
      {children}
    </select>
  );
}

export function Slider({
  value,
  onChange,
  min,
  max,
  step = 1,
  format,
  id,
}: {
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step?: number;
  format?: (v: number) => string;
  id?: string;
}) {
  const pct = max > min ? Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100)) : 0;
  return (
    <div className="flex items-center gap-3">
      <div className="liquid w-full">
        <div className="liquid-fill" style={{ width: `${pct}%` }} />
        <input
          id={id}
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
        />
      </div>
      <span className="tabular w-16 shrink-0 text-right text-sm">{format ? format(value) : value}</span>
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: ReactNode;
}) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-3 text-sm select-none">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={cx(
          "relative h-6 w-11 shrink-0 rounded-full border border-border transition",
          checked ? "bg-accent" : "bg-surface-2",
        )}
      >
        <span
          className={cx(
            "absolute top-0.5 size-[18px] rounded-full bg-white shadow transition-all",
            checked ? "left-[22px]" : "left-0.5",
          )}
        />
      </button>
      {label}
    </label>
  );
}

// ---------- feedback ----------

type Tone = "neutral" | "ok" | "warn" | "danger" | "accent";
const TONES: Record<Tone, string> = {
  neutral: "bg-surface-2 text-muted border-border",
  ok: "bg-ok-bg text-ok border-ok/30",
  warn: "bg-warn-bg text-warn border-warn/30",
  danger: "bg-danger-bg text-danger border-danger/30",
  accent: "bg-accent-bg text-accent border-accent/30",
};

export function Badge({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium whitespace-nowrap",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-8 text-sm text-muted">
      <Loader2 className="size-4 animate-spin" /> {label}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="flex items-start gap-2 rounded-lg border border-danger/30 bg-danger-bg px-3 py-2 text-sm text-danger">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
      <span className="min-w-0 flex-1 break-words">{msg}</span>
      {onRetry && (
        <button className="underline" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
      {icon && <div className="text-muted">{icon}</div>}
      <p className="font-medium">{title}</p>
      {children && <div className="max-w-md text-sm text-muted">{children}</div>}
    </div>
  );
}

// ---------- dialogs ----------

export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    panel.current?.querySelector<HTMLElement>("input, textarea, select")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-0 sm:items-center sm:p-4" onMouseDown={onClose}>
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(e) => e.stopPropagation()}
        className={cx(
          "flex max-h-[92vh] w-full flex-col rounded-t-2xl border border-border bg-surface shadow-2xl sm:rounded-2xl",
          wide ? "sm:max-w-2xl" : "sm:max-w-md",
        )}
      >
        <header className="flex items-center justify-between border-b border-border px-5 py-3">
          <h2 id={titleId} className="font-semibold">
            {title}
          </h2>
          <button className="rounded p-1 text-muted hover:bg-surface-2 hover:text-fg" onClick={onClose} aria-label="Close">
            <X className="size-4" />
          </button>
        </header>
        <div className="overflow-y-auto px-5 py-4">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-border px-5 py-3">{footer}</footer>}
      </div>
    </div>
  );
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  danger,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => Promise<unknown> | void;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    if (open) setErr(null);
  }, [open]);
  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      await onConfirm();
      onClose();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant={danger ? "danger" : "primary"} loading={busy} onClick={run}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-sm">
        <div>{message}</div>
        <ErrorBox error={err} />
      </div>
    </Dialog>
  );
}

// ---------- data loading ----------

/** Minimal async loader: {data, error, loading, reload, setData}. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    let alive = true;
    setLoading(true);
    fnRef
      .current()
      .then((d) => {
        if (!alive) return;
        setData(d);
        setError(null);
      })
      .catch((e) => alive && setError(e))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, loading, reload: () => setTick((t) => t + 1), setData };
}

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx("-mx-4 overflow-x-auto px-4", className)}>
      <table className="w-full min-w-max text-left text-sm [&_td]:px-3 [&_td]:py-2 [&_th]:px-3 [&_th]:py-2 [&_th]:text-xs [&_th]:font-medium [&_th]:text-muted [&_thead_tr]:border-b [&_thead_tr]:border-border [&_tbody_tr]:border-b [&_tbody_tr]:border-border/60 [&_tbody_tr:last-child]:border-0">
        {children}
      </table>
    </div>
  );
}
