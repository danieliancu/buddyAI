import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { Info, Package, Truck } from "lucide-react";
import { api, type Order, type OrderStatus, type OrderUpdate } from "../api";
import { fmtDateTime, fmtMoney } from "../format";
import { OrderStatusBadge } from "../components/BillingBits";
import { Button, Card, Dialog, Empty, ErrorBox, Field, Input, PageHeader, Select, Spinner, Table, useAsync } from "../components/ui";

/** Amount in minor units (pence / cents) → "£149.00". */
export const fmtMinor = (amount: number, currency: string) => fmtMoney(amount / 100, currency.toUpperCase());

export default function OrdersPage() {
  const [status, setStatus] = useState<OrderStatus | "">("");
  const orders = useAsync(() => api.orders.list(status), [status]);
  const [editing, setEditing] = useState<Order | null>(null);
  const list = orders.data ?? [];

  return (
    <>
      <PageHeader
        title="Orders"
        subtitle={orders.data ? `${list.length} ${list.length === 1 ? "order" : "orders"}` : undefined}
        actions={
          <Select className="w-auto" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            <option value="">All statuses</option>
            <option value="paid">Paid (to ship)</option>
            <option value="shipped">Shipped</option>
            <option value="delivered">Delivered</option>
            <option value="refunded">Refunded</option>
            <option value="cancelled">Cancelled</option>
          </Select>
        }
      />
      <p className="mb-4 flex items-center gap-2 text-sm text-muted">
        <Info className="size-4 shrink-0" /> Refunds are made in the Stripe dashboard; the order is then marked refunded automatically.
      </p>
      <ErrorBox error={orders.error} onRetry={orders.reload} />
      {orders.loading && !orders.data ? (
        <Spinner />
      ) : list.length === 0 ? (
        <Card>
          <Empty icon={<Package className="size-8" />} title={status ? "No orders with this status" : "No orders yet"}>
            Orders appear here when a customer completes the Stripe checkout.
          </Empty>
        </Card>
      ) : (
        <Card bodyClassName="p-0 px-4">
          <Table>
            <thead>
              <tr>
                <th>#</th>
                <th>Date</th>
                <th>Customer</th>
                <th>Country</th>
                <th className="text-right">Amount</th>
                <th>Status</th>
                <th>Tracking</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {list.map((o) => (
                <tr key={o.id}>
                  <td className="tabular">{o.id}</td>
                  <td className="whitespace-nowrap">{fmtDateTime(o.created_at)}</td>
                  <td>
                    {o.account_id ? (
                      <Link to={`/admin/customers/${o.account_id}`} className="text-accent hover:underline">
                        {o.email}
                      </Link>
                    ) : (
                      o.email
                    )}
                    {o.shipping_name && <span className="block text-xs text-muted">{o.shipping_name}</span>}
                  </td>
                  <td>{o.country ?? "—"}</td>
                  <td className="tabular text-right">{fmtMinor(o.amount_total, o.currency)}</td>
                  <td>
                    <OrderStatusBadge status={o.status} operator />
                  </td>
                  <td className="text-xs">
                    {o.tracking_number ? (
                      <>
                        {o.carrier && <span className="text-muted">{o.carrier} </span>}
                        <span className="font-mono">{o.tracking_number}</span>
                      </>
                    ) : (
                      <span className="text-muted">—</span>
                    )}
                  </td>
                  <td className="text-right">
                    {o.status === "paid" ? (
                      <Button size="sm" variant="primary" icon={<Truck className="size-3.5" />} onClick={() => setEditing(o)}>
                        Mark shipped
                      </Button>
                    ) : o.status !== "refunded" ? (
                      <Button size="sm" onClick={() => setEditing(o)}>
                        Edit
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <OrderDialog
        order={editing}
        onClose={() => setEditing(null)}
        onSaved={(o) => orders.setData((l) => l?.map((x) => (x.id === o.id ? o : x)) ?? l)}
      />
    </>
  );
}

export function OrderDialog({ order, onClose, onSaved }: { order: Order | null; onClose: () => void; onSaved: (o: Order) => void }) {
  const [status, setStatus] = useState<OrderUpdate["status"]>("shipped");
  const [carrier, setCarrier] = useState("");
  const [tracking, setTracking] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (!order) return;
    setStatus(order.status === "paid" ? "shipped" : (order.status as OrderUpdate["status"]));
    setCarrier(order.carrier);
    setTracking(order.tracking_number);
    setError(null);
  }, [order]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!order) return;
    if (status === "shipped" && !tracking.trim()) return setError(new Error("Enter the tracking number."));
    setBusy(true);
    setError(null);
    try {
      onSaved(await api.orders.update(order.id, { status, carrier: carrier.trim(), tracking_number: tracking.trim() }));
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const firstShip = order?.status === "paid" && status === "shipped";

  return (
    <Dialog
      open={!!order}
      onClose={onClose}
      title={order?.status === "paid" ? `Ship order #${order?.id}` : `Order #${order?.id}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="order-form" loading={busy}>
            {firstShip ? "Mark shipped" : "Save"}
          </Button>
        </>
      }
    >
      <form id="order-form" onSubmit={submit} className="space-y-4">
        <p className="text-sm text-muted">
          {order?.email} · {order && fmtMinor(order.amount_total, order.currency)}
        </p>
        <Field label="Status" htmlFor="o-status">
          <Select id="o-status" value={status} onChange={(e) => setStatus(e.target.value as OrderUpdate["status"])}>
            <option value="paid">Paid (not shipped)</option>
            <option value="shipped">Shipped</option>
            <option value="delivered">Delivered</option>
            <option value="cancelled">Cancelled</option>
          </Select>
        </Field>
        <div className="grid gap-4 sm:grid-cols-[160px_minmax(0,1fr)]">
          <Field label="Carrier" htmlFor="o-carrier">
            <Input id="o-carrier" maxLength={60} placeholder="Royal Mail" value={carrier} onChange={(e) => setCarrier(e.target.value)} />
          </Field>
          <Field label="Tracking number" htmlFor="o-track">
            <Input id="o-track" maxLength={120} value={tracking} onChange={(e) => setTracking(e.target.value)} className="font-mono" />
          </Field>
        </div>
        {firstShip && <p className="text-xs text-muted">The customer receives an email with the tracking number.</p>}
        <ErrorBox error={error} />
      </form>
    </Dialog>
  );
}
