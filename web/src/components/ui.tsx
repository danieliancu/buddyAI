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
import { AlertTriangle, Eye, EyeOff, Loader2, X } from "lucide-react";
import { useArea } from "../area";

export function cx(...c: (string | false | null | undefined)[]): string {
  return c.filter(Boolean).join(" ");
}

// ---------- buttons ----------

type Variant = "primary" | "secondary" | "ghost" | "danger";

const VARIANTS: Record<Variant, string> = {
  primary: "btn-primary hover:brightness-110 border-transparent",
  secondary: "bg-surface-2 text-fg hover:bg-border/60 border-border",
  ghost: "bg-transparent text-fg hover:bg-surface-2 border-transparent",
  danger: "bg-danger-bg text-danger hover:bg-danger hover:text-white border-danger/30",
};

type Size = "sm" | "md" | "lg";

export function buttonCls(variant: Variant = "secondary", size: Size = "md", className?: string): string {
  return cx(
    "inline-flex cursor-pointer items-center justify-center gap-2 rounded-(--r-btn) border [font-weight:var(--btn-weight)] transition",
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
    "disabled:cursor-not-allowed disabled:opacity-50",
    size === "sm" ? "h-8 px-3 text-xs" : size === "lg" ? "h-12 px-6 text-base [&_svg]:size-5" : "h-(--h-btn) px-(--btn-px) text-sm",
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
  size?: Size;
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

/** A round icon-only button (edit, delete…): neutral, or pink for a destructive action. */
export function IconButton({
  label,
  danger,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; danger?: boolean }) {
  return (
    <button
      type="button"
      {...rest}
      aria-label={label}
      title={label}
      className={cx(
        "grid size-10 shrink-0 cursor-pointer place-items-center rounded-full transition disabled:opacity-50",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        danger ? "bg-danger-bg text-danger hover:bg-danger hover:text-white" : "bg-surface-2 text-fg/80 hover:bg-border hover:text-fg",
        className,
      )}
    >
      {children}
    </button>
  );
}

// ---------- layout ----------

/** The light-blue rounded square that holds a card's or a row's icon. */
export function IconTile({ children, size = "md", className }: { children: ReactNode; size?: "sm" | "md" | "lg"; className?: string }) {
  return (
    <span
      className={cx(
        "grid shrink-0 place-items-center bg-accent-bg text-accent",
        size === "sm" ? "size-9 rounded-xl" : size === "md" ? "size-12 rounded-2xl" : "size-14 rounded-2xl",
        className,
      )}
    >
      {children}
    </span>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
  count,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  /** A small counter next to the title (e.g. how many watches). */
  count?: number;
}) {
  if (useArea() === "me") {
    // Customer area: a large bold title, the action (e.g. "+ Add watch") beside it.
    return (
      <div className="mb-6 flex items-center justify-between gap-4">
        <div className="min-w-0">
          <h1 className="flex items-center gap-3 text-[2.1rem] leading-tight font-bold tracking-tight sm:text-[2.6rem]">
            {title}
            {count != null && (
              <span className="rounded-full bg-accent-bg px-2.5 py-0.5 text-sm font-semibold text-accent tabular-nums">{count}</span>
            )}
          </h1>
          {subtitle && <div className="mt-1.5 text-base text-muted sm:text-lg">{subtitle}</div>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
    );
  }
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="flex items-center gap-3 text-2xl font-semibold tracking-tight sm:text-[1.9rem]">
          {title}
          {count != null && (
            <span className="rounded-md bg-surface-2 px-1.5 py-0.5 text-xs font-medium text-muted tabular-nums">{count}</span>
          )}
        </h1>
        {subtitle && <div className="mt-2 text-[15px] text-muted">{subtitle}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/** The card surface (rounded, soft shadow in the customer area; flat with a border for the operator). */
export const cardCls = "min-w-0 rounded-(--r-card) border border-(--card-border) bg-surface shadow-(--card-shadow)";

export function Card({
  title,
  icon,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  /** Before the title: in an IconTile in the customer area, a small accent icon for the operator. */
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  const me = useArea() === "me";
  return (
    <section className={cx(cardCls, className)}>
      {(title || actions) && (
        <header
          className={cx(
            "flex flex-wrap items-center justify-between gap-2 border-b border-border",
            me ? "px-(--card-pad) py-4" : "px-4 py-3",
          )}
        >
          {title && (
            <h2 className={cx("flex min-w-0 items-center", me ? "gap-4 text-lg font-bold" : "gap-2 text-sm font-semibold")}>
              {icon && (me ? <IconTile className="[&_svg]:size-6">{icon}</IconTile> : <span className="text-accent [&_svg]:size-4">{icon}</span>)}
              {title}
            </h2>
          )}
          {actions}
        </header>
      )}
      <div className={cx("p-(--card-pad)", bodyClassName)}>{children}</div>
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
  "w-full rounded-(--r-input) border border-border bg-(--input-bg) px-3 text-sm text-fg placeholder:text-muted/70 " +
  "focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25 disabled:opacity-60";

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...rest} className={cx(inputCls, "h-(--h-input)", className)} />;
}

/** A password field with a show / hide button (the eye). */
export function PasswordInput({ className, ...rest }: Omit<InputHTMLAttributes<HTMLInputElement>, "type">) {
  const [shown, setShown] = useState(false);
  return (
    <div className="relative">
      <input {...rest} type={shown ? "text" : "password"} className={cx(inputCls, "h-(--h-input) pr-11", className)} />
      <button
        type="button"
        onClick={() => setShown((v) => !v)}
        className="absolute inset-y-0 right-0 grid w-10 cursor-pointer place-items-center text-muted hover:text-fg"
        aria-label={shown ? "Hide password" : "Show password"}
        aria-pressed={shown}
        title={shown ? "Hide password" : "Show password"}
      >
        {shown ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
      </button>
    </div>
  );
}

export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...rest} className={cx(inputCls, "min-h-24 py-2 leading-relaxed", className)} />;
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...rest} className={cx(inputCls, "select-chevron h-(--h-input) pr-10", className)}>
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

const DOTS: Record<Tone, string> = { neutral: "bg-muted", ok: "bg-ok", warn: "bg-warn", danger: "bg-danger", accent: "bg-accent" };

export function Badge({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  if (useArea() === "admin") {
    // Operator area: a white pill with a coloured status dot.
    return (
      <span
        className={cx(
          "inline-flex items-center gap-1.5 rounded-md border border-border bg-surface px-2 py-0.5 text-xs font-medium whitespace-nowrap text-fg",
          className,
        )}
      >
        <span className={cx("size-1.5 shrink-0 rounded-full", DOTS[tone])} />
        {children}
      </span>
    );
  }
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[13px] leading-none font-medium whitespace-nowrap [&_svg]:size-3.5",
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
    <div className="flex items-start gap-2 rounded-(--r-input) border border-danger/30 bg-danger-bg px-3 py-2 text-sm text-danger">
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
  const me = useArea() === "me";
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
      {icon && (me ? <IconTile size="lg" className="mb-1 [&_svg]:size-7">{icon}</IconTile> : <div className="text-muted">{icon}</div>)}
      <p className={me ? "text-base font-semibold" : "font-medium"}>{title}</p>
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
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 backdrop-blur-[2px] sm:items-center sm:p-4" onMouseDown={onClose}>
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(e) => e.stopPropagation()}
        className={cx(
          "flex max-h-[92vh] w-full flex-col rounded-t-3xl border border-(--card-border) bg-surface shadow-2xl sm:rounded-3xl",
          wide ? "sm:max-w-2xl" : "sm:max-w-md",
        )}
      >
        <header className="flex items-center justify-between border-b border-border px-5 py-4">
          <h2 id={titleId} className="text-lg font-bold">
            {title}
          </h2>
          <button className="grid size-8 cursor-pointer place-items-center rounded-full text-muted hover:bg-surface-2 hover:text-fg" onClick={onClose} aria-label="Close">
            <X className="size-4" />
          </button>
        </header>
        <div className="overflow-y-auto px-5 py-4">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-border px-5 py-4">{footer}</footer>}
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
      <table className="w-full min-w-max text-left text-sm [&_td]:px-4 [&_td]:py-3 [&_th]:px-4 [&_th]:py-2.5 [&_th]:text-[13px] [&_th]:font-medium [&_th]:text-muted [&_thead_tr]:border-b [&_thead_tr]:border-border [&_thead_tr]:bg-surface-2/70 [&_th+th]:border-l [&_th+th]:border-border [&_td+td]:border-l [&_td+td]:border-border/70 [&_tbody_tr]:border-b [&_tbody_tr]:border-border/70 [&_tbody_tr:last-child]:border-0 [&_tbody_tr:hover]:bg-surface-2/40">
        {children}
      </table>
    </div>
  );
}
