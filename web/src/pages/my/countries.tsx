import { useMemo } from "react";
import { Select } from "../../components/ui";

// Countries we ship to first (UK + EU/EEA + a few others); names come from Intl.DisplayNames.
const CODES = [
  "GB", "IE", "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IT", "LV", "LT", "LU",
  "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "NO", "IS", "CH", "MD", "UA", "RS", "TR", "US", "CA", "AU", "NZ",
];

function regionName(code: string): string {
  try {
    return new Intl.DisplayNames(["en"], { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
}

/** Country select (ISO alpha-2); "" = not set. Keeps an unknown current value selectable. */
export function CountrySelect({ id, value, onChange }: { id?: string; value: string; onChange: (code: string) => void }) {
  const options = useMemo(() => {
    const codes = value && !CODES.includes(value) ? [...CODES, value] : CODES;
    return codes.map((c) => ({ code: c, name: regionName(c) })).sort((a, b) => a.name.localeCompare(b.name));
  }, [value]);
  return (
    <Select id={id} value={value} onChange={(e) => onChange(e.target.value)} autoComplete="country">
      <option value="">Not set</option>
      {options.map((o) => (
        <option key={o.code} value={o.code}>
          {o.name}
        </option>
      ))}
    </Select>
  );
}
