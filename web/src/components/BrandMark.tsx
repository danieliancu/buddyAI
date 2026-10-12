import { useId } from "react";

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
