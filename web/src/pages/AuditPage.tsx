import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { ScrollText } from "lucide-react";
import { api, type AuditFilters } from "../api";
import { fmtDateTime } from "../format";
import { Button, Card, Empty, ErrorBox, Field, Input, PageHeader, Select, Spinner, Table, useAsync } from "../components/ui";

const PAGE = 100;

/** Action groups for the filter (prefixes of AuditLog.action). */
const GROUPS: { value: string; label: string }[] = [
  { value: "", label: "All actions" },
  { value: "operator.", label: "Operator sign-in & security" },
  { value: "account.", label: "Customer accounts" },
  { value: "history.", label: "Conversation history" },
  { value: "order.", label: "Orders" },
  { value: "subscription.", label: "Subscriptions" },
  { value: "billing.", label: "Billing settings" },
  { value: "pricing.", label: "Pricing" },
  { value: "system.", label: "System (provider keys)" },
  { value: "firmware.", label: "Firmware & OTA" },
  { value: "incidents.", label: "Diagnostics: incidents" },
  { value: "issues.", label: "Diagnostics: issues" },
];

/** The global audit log (admin role): who did what, when. Newest first. */
export default function AuditPage() {
  const [filters, setFilters] = useState<AuditFilters>({});
  const [offset, setOffset] = useState(0);
  const [actor, setActor] = useState("");
  const [account, setAccount] = useState("");
  const page = useAsync(() => api.audit({ ...filters, limit: PAGE, offset }), [filters, offset]);

  const apply = (e: FormEvent) => {
    e.preventDefault();
    const id = Number(account.trim());
    setOffset(0);
    setFilters((f) => ({ ...f, actor: actor.trim() || undefined, account_id: account.trim() && Number.isInteger(id) ? id : undefined }));
  };

  const d = page.data;
  return (
    <>
      <PageHeader
        title="Audit log"
        count={d?.total}
        subtitle="Operator actions, operator sign-ins and customer account changes. Passwords, codes and key values are never recorded."
      />
      <Card className="mb-4">
        <form onSubmit={apply} className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,140px)_auto] sm:items-end">
          <Field label="Action" htmlFor="audit-action">
            <Select
              id="audit-action"
              value={filters.action ?? ""}
              onChange={(e) => {
                setOffset(0);
                setFilters((f) => ({ ...f, action: e.target.value || undefined }));
              }}
            >
              {GROUPS.map((g) => (
                <option key={g.value} value={g.value}>
                  {g.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Actor" htmlFor="audit-actor">
            <Input id="audit-actor" placeholder="operator name, account:<id> or system" value={actor} onChange={(e) => setActor(e.target.value)} />
          </Field>
          <Field label="Account ID" htmlFor="audit-account">
            <Input id="audit-account" inputMode="numeric" value={account} onChange={(e) => setAccount(e.target.value)} />
          </Field>
          <Button type="submit">Filter</Button>
        </form>
      </Card>
      <ErrorBox error={page.error} onRetry={page.reload} />
      {page.loading && !d ? (
        <Spinner />
      ) : !d || d.events.length === 0 ? (
        <Card>
          <Empty icon={<ScrollText className="size-8" />} title="No audit events">
            Nothing matches these filters.
          </Empty>
        </Card>
      ) : (
        <Card bodyClassName="p-0 px-4">
          <Table>
            <thead>
              <tr>
                <th>When</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Account</th>
                <th>Watch</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {d.events.map((e) => (
                <tr key={e.id}>
                  <td className="whitespace-nowrap">{fmtDateTime(e.created_at)}</td>
                  <td className="font-mono text-xs">{e.actor}</td>
                  <td className="font-mono text-xs">{e.action}</td>
                  <td>
                    {e.account_id != null ? (
                      <Link to={`/admin/customers/${e.account_id}`} className="text-accent hover:underline">
                        {e.account_id}
                      </Link>
                    ) : (
                      <span className="text-muted">—</span>
                    )}
                  </td>
                  <td className="font-mono text-xs">{e.device_id ?? <span className="text-muted">—</span>}</td>
                  <td className="max-w-md text-xs break-words whitespace-normal">{e.detail || <span className="text-muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
      {d && d.total > PAGE && (
        <div className="mt-4 flex items-center justify-between text-sm text-muted">
          <span>
            {d.offset + 1}–{Math.min(d.offset + d.events.length, d.total)} of {d.total}
          </span>
          <div className="flex gap-2">
            <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              Newer
            </Button>
            <Button size="sm" disabled={offset + PAGE >= d.total} onClick={() => setOffset(offset + PAGE)}>
              Older
            </Button>
          </div>
        </div>
      )}
    </>
  );
}
