import { useState, type FormEvent } from "react";
import { Info, Plus, TicketPercent } from "lucide-react";
import { api, type DiscountCode } from "../api";
import { fmtDateTime } from "../format";
import { Badge, Button, Card, Dialog, Empty, ErrorBox, Field, Input, PageHeader, Spinner, Table, Toggle, useAsync } from "../components/ui";
import { fmtMinor } from "./OrdersPage";

const gbp = (pence: number) => fmtMinor(pence, "gbp");

/**
 * /admin/discounts: codes the buyer types at Stripe Checkout (e.g. VIP → the watch at £79.99). Each code is a Stripe
 * coupon limited to the watch (never ola Care) plus its promotion code; the list, uses and on/off state come from
 * Stripe. The operator gives the price with the code; the server works out the amount off.
 */
export default function DiscountsPage() {
  const data = useAsync(() => api.discounts.list(), []);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const codes = data.data?.codes ?? [];

  const toggle = async (c: DiscountCode, active: boolean) => {
    setBusy(c.id);
    setError(null);
    try {
      const row = await api.discounts.setActive(c.id, active);
      data.setData((d) => d && { ...d, codes: d.codes.map((x) => (x.id === row.id ? row : x)) });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <PageHeader
        title="Discount codes"
        count={data.data ? codes.length : undefined}
        subtitle={data.data ? `Watch price ${gbp(data.data.watch_price_pence)}. Buyers type a code at checkout.` : "Codes buyers type at checkout."}
        actions={
          <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setCreating(true)} disabled={!data.data}>
            New code
          </Button>
        }
      />
      <p className="mb-4 flex items-center gap-2 text-sm text-muted">
        <Info className="size-4 shrink-0" /> A code lowers the watch price only. The olá Care subscription and its free trial are not affected.
      </p>
      <ErrorBox error={data.error ?? error} onRetry={data.error ? data.reload : undefined} />
      {data.loading && !data.data ? (
        <Spinner />
      ) : !data.data ? null : codes.length === 0 ? (
        <Card>
          <Empty icon={<TicketPercent className="size-8" />} title="No discount codes yet">
            Create one, for example VIP with the price £79.99.
          </Empty>
        </Card>
      ) : (
        <Card bodyClassName="p-0 px-4">
          <Table>
            <thead>
              <tr>
                <th>Code</th>
                <th className="text-right">Price with code</th>
                <th className="text-right">Uses</th>
                <th>Limits</th>
                <th>Created</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {codes.map((c) => (
                <tr key={c.id}>
                  <td className="font-mono font-semibold">{c.code}</td>
                  <td className="tabular text-right">
                    {gbp(c.final_price_pence)}
                    {c.amount_off_pence > 0 && <span className="block text-xs text-muted">−{gbp(c.amount_off_pence)}</span>}
                  </td>
                  <td className="tabular text-right">{c.times_redeemed}</td>
                  <td className="text-xs text-muted">
                    {c.max_redemptions || c.expires_at ? (
                      <>
                        {c.max_redemptions && <span className="block">Up to {c.max_redemptions} uses</span>}
                        {c.expires_at && <span className="block">Until {fmtDateTime(c.expires_at)}</span>}
                      </>
                    ) : (
                      "None"
                    )}
                  </td>
                  <td className="whitespace-nowrap">{c.created_at ? fmtDateTime(c.created_at) : "—"}</td>
                  <td>
                    {busy === c.id ? (
                      <span className="text-sm text-muted">Saving…</span>
                    ) : (
                      <Toggle
                        checked={c.active}
                        onChange={(v) => toggle(c, v)}
                        label={c.active ? <Badge tone="ok">Active</Badge> : <Badge>Off</Badge>}
                      />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <NewCodeDialog
        open={creating}
        watchPence={data.data?.watch_price_pence ?? 0}
        onClose={() => setCreating(false)}
        onCreated={(row) => data.setData((d) => d && { ...d, codes: [row, ...d.codes] })}
      />
    </>
  );
}

function NewCodeDialog({
  open,
  watchPence,
  onClose,
  onCreated,
}: {
  open: boolean;
  watchPence: number;
  onClose: () => void;
  onCreated: (row: DiscountCode) => void;
}) {
  const [code, setCode] = useState("");
  const [price, setPrice] = useState("");
  const [limits, setLimits] = useState(false);
  const [maxUses, setMaxUses] = useState("");
  const [expires, setExpires] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const pence = Math.round(parseFloat(price.replace(",", ".")) * 100);
  const valid = /^[A-Z0-9]{3,20}$/.test(code) && Number.isFinite(pence) && pence >= 100 && pence < watchPence;

  const reset = () => {
    setCode("");
    setPrice("");
    setLimits(false);
    setMaxUses("");
    setExpires("");
    setError(null);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!valid) return setError(new Error(`Use 3–20 letters or digits, and a price from £1.00 to under ${fmtMinor(watchPence, "gbp")}.`));
    setBusy(true);
    setError(null);
    try {
      const row = await api.discounts.create({
        code,
        final_price_pence: pence,
        max_redemptions: limits && maxUses ? parseInt(maxUses, 10) : null,
        expires_at: limits && expires ? new Date(`${expires}T23:59:59`).toISOString() : null,
      });
      onCreated(row);
      reset();
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="New discount code"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="discount-form" loading={busy}>
            Create code
          </Button>
        </>
      }
    >
      <form id="discount-form" onSubmit={submit} className="space-y-4">
        <Field label="Code" htmlFor="d-code" hint="What the buyer types at checkout. Letters and digits only.">
          <Input
            id="d-code"
            maxLength={20}
            placeholder="VIP"
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, ""))}
            className="font-mono"
          />
        </Field>
        <Field
          label="Watch price with this code (£)"
          htmlFor="d-price"
          hint={
            Number.isFinite(pence) && pence > 0 && pence < watchPence
              ? `${fmtMinor(watchPence - pence, "gbp")} off the ${fmtMinor(watchPence, "gbp")} price.`
              : `The watch costs ${fmtMinor(watchPence, "gbp")} without a code.`
          }
        >
          <Input id="d-price" inputMode="decimal" placeholder="79.99" value={price} onChange={(e) => setPrice(e.target.value)} />
        </Field>
        <Toggle checked={limits} onChange={setLimits} label="Limits (optional)" />
        {limits && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Maximum uses" htmlFor="d-max">
              <Input id="d-max" inputMode="numeric" placeholder="No limit" value={maxUses} onChange={(e) => setMaxUses(e.target.value.replace(/\D/g, ""))} />
            </Field>
            <Field label="Valid until" htmlFor="d-exp">
              <Input id="d-exp" type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />
            </Field>
          </div>
        )}
        <ErrorBox error={error} />
      </form>
    </Dialog>
  );
}
