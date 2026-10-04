import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { Plus, Search, Users } from "lucide-react";
import { api, ApiError, type AccountStatus } from "../api";
import { fmtAgo, fmtDateTime, fmtMoney } from "../format";
import { CountrySelect } from "./my/countries";
import { Badge, Button, Card, Dialog, Empty, ErrorBox, Field, Input, PageHeader, Select, Spinner, Table, useAsync } from "../components/ui";

export function AccountStatusBadge({ status }: { status: string }) {
  return status === "active" ? <Badge tone="ok">Active</Badge> : status === "suspended" ? <Badge tone="danger">Suspended</Badge> : <Badge>{status}</Badge>;
}

export default function CustomersPage() {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<AccountStatus | "">("");
  const [creating, setCreating] = useState(false);
  const list = useAsync(() => api.accounts.list(query, status), [query, status]);

  // Debounced search.
  useEffect(() => {
    const t = window.setTimeout(() => setQuery(q.trim()), 300);
    return () => window.clearTimeout(t);
  }, [q]);

  const rows = list.data?.accounts ?? [];
  const currency = list.data?.currency ?? "GBP";

  return (
    <>
      <PageHeader
        title="Customers"
        count={list.data ? rows.length : undefined}
        subtitle={`Customer accounts, their plans and watches.${rows.length >= 500 ? " Showing the first 500." : ""}`}
        actions={
          <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setCreating(true)}>
            Create account
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap gap-2">
        <div className="relative min-w-0 flex-1 basis-56">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
          <Input type="search" className="pl-9" placeholder="Search email or name" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search" />
        </div>
        <Select className="w-auto" value={status} onChange={(e) => setStatus(e.target.value as AccountStatus | "")} aria-label="Status">
          <option value="">All statuses</option>
          <option value="active">Active</option>
          <option value="suspended">Suspended</option>
        </Select>
      </div>

      <ErrorBox error={list.error} onRetry={list.reload} />
      {list.loading && !list.data ? (
        <Spinner />
      ) : rows.length === 0 ? (
        <Card>
          <Empty icon={<Users className="size-8" />} title={query || status ? "No matching accounts" : "No customers yet"} />
        </Card>
      ) : (
        <Card bodyClassName="p-0 px-4">
          <Table>
            <thead>
              <tr>
                <th>Email</th>
                <th>Name</th>
                <th>Country</th>
                <th>Verified</th>
                <th>Status</th>
                <th className="text-right">Watches</th>
                <th className="text-right">This month</th>
                <th>Last login</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.id} className="hover:bg-surface-2/50">
                  <td>
                    <Link to={`/admin/customers/${a.id}`} className="font-medium text-accent hover:underline">
                      {a.email}
                    </Link>
                  </td>
                  <td>{a.name || <span className="text-muted">—</span>}</td>
                  <td>{a.country ?? <span className="text-muted">—</span>}</td>
                  <td>{a.email_verified ? <Badge tone="ok">Yes</Badge> : <Badge tone="warn">No</Badge>}</td>
                  <td>
                    <AccountStatusBadge status={a.status} />
                  </td>
                  <td className="tabular text-right">{a.devices}</td>
                  <td className="tabular text-right">{fmtMoney(a.month_cost, currency)}</td>
                  <td title={fmtDateTime(a.last_login_at)}>{a.last_login_at ? fmtAgo(a.last_login_at) : <span className="text-muted">never</span>}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <CreateAccountDialog open={creating} onClose={() => setCreating(false)} />
    </>
  );
}

function CreateAccountDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [country, setCountry] = useState("GB");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (open) {
      setEmail("");
      setName("");
      setCountry("GB");
      setError(null);
    }
  }, [open]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return setError(new Error("Email is required."));
    setBusy(true);
    setError(null);
    try {
      const a = await api.accounts.create({ email: email.trim(), name: name.trim(), country: country || null });
      onClose();
      navigate(`/admin/customers/${a.id}`);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 409 ? new Error("An account with this email already exists.") : err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Create account"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="create-acc" loading={busy}>
            Create and send email
          </Button>
        </>
      }
    >
      <form id="create-acc" onSubmit={submit} className="space-y-4">
        <p className="text-sm text-muted">The customer receives an email with a link to set their password (valid for 1 hour).</p>
        <Field label="Email" htmlFor="ca-email">
          <Input id="ca-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Name" htmlFor="ca-name">
          <Input id="ca-name" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Country" htmlFor="ca-country">
          <CountrySelect id="ca-country" value={country} onChange={setCountry} />
        </Field>
        <ErrorBox error={error} />
      </form>
    </Dialog>
  );
}
