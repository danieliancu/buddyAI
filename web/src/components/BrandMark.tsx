import { useId } from "react";

/**
 * The website's whole logo (site/src/components/Logo.astro, size "md"), pixel for pixel: the 26 px mark and
 * "olácompanion" with the leaf over the "a". Colours come from `.site-header` (index.css).
 */
export function SiteLogo() {
  return (
    <span className="inline-flex items-center gap-2">
      <BrandMark size={26} />
      <span className="text-xl font-bold tracking-tight text-(--sh-fg)">
        ol
        <span className="relative">
          a
          <svg viewBox="0 0 16 16" className="absolute -top-[2px] left-[2px] h-[0.55em] w-[0.55em] text-(--sh-accent)" aria-hidden="true">
            <path d="M2.5 13.5C2.5 7 6.8 2.6 14 2c.4 7-4.2 11.5-11.5 11.5z" fill="currentColor" />
            <path d="M2.5 13.5 9.5 6.5" stroke="var(--sh-leaf-cut)" strokeWidth="1.1" strokeLinecap="round" />
          </svg>
        </span>
        <span className="text-(--sh-accent)">companion</span>
      </span>
    </span>
  );
}

/** The olá mark from the website (site/src/components/Logo.astro): five sound-wave bars, violet to blue. */
export default function BrandMark({ size = 34 }: { size?: number }) {
  const id = useId();
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={id} x1="0" x2="1">
          <stop offset="0" stopColor="#8b7bff" />
          <stop offset="1" stopColor="#4ea3ff" />
        </linearGradient>
      </defs>
      <g stroke={`url(#${id})`} strokeWidth="3.2" strokeLinecap="round">
        <path d="M4 13v6M10 9v14M16 5v22M22 9v14M28 13v6" />
      </g>
    </svg>
  );
}
