import { useEffect, useState } from "react";
import { api, type AccountRow } from "../api";
import { Input, Select } from "./ui";

/** Choose a customer account (or none = operator stock). Searches by email/name on the server. */
export function AccountPicker({
  value,
  onChange,
  noneLabel = "Stock (no customer)",
  id,
}: {
  value: number | null;
  onChange: (id: number | null) => void;
  noneLabel?: string;
  id?: string;
}) {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<AccountRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const t = window.setTimeout(() => {
      api.accounts
        .list(q.trim(), "active")
        .then((r) => {
          if (!alive) return;
          setRows(r.accounts);
          setError(null);
        })
        .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    }, 250);
    return () => {
      alive = false;
      window.clearTimeout(t);
    };
  }, [q]);

  const selectedMissing = value != null && !rows.some((r) => r.id === value);

  return (
    <div className="space-y-2">
      <Input type="search" placeholder="Search customers by email or name" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search customers" />
      <Select id={id} value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
        <option value="">{noneLabel}</option>
        {selectedMissing && <option value={value}>Account #{value}</option>}
        {rows.map((a) => (
          <option key={a.id} value={a.id}>
            {a.email}
            {a.name ? ` — ${a.name}` : ""}
          </option>
        ))}
      </Select>
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  );
}
