import { useState, type FormEvent } from "react";
import { api, ApiError, type CareActivation } from "../../api";
import { Button, Card, ErrorBox, Field, Input } from "../../components/ui";

/** The 6-digit pairing code from the watch screen. Pairing links the watch to this account (server side),
 * which is also what starts a pending ola Care trial. */
export default function PairCodeForm({
  onPaired,
}: {
  onPaired: (deviceId: string, care: CareActivation | null) => void;
}) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("My ola");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!/^\d{6}$/.test(code)) return setError(new Error("The code has 6 digits — you'll find it on the watch screen."));
    setBusy(true);
    setError(null);
    try {
      const r = await api.me.devices.pair(code, name.trim() || "My ola");
      onPaired(r.device_id, r.care);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403)
        setError(
          new Error(
            /order/i.test(String(err.detail ?? ""))
              ? "We couldn't find a watch order for this account. Sign in with the email you used to buy the watch."
              : "Please confirm your email address first.",
          ),
        );
      else setError(err);
      setBusy(false);
    }
  };

  return (
    <Card>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Code shown on the watch" htmlFor="pair-code">
          <Input
            id="pair-code"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            placeholder="000000"
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
            className="h-16 text-center font-mono text-3xl tracking-[0.4em]"
          />
        </Field>
        <Field label="Watch name" htmlFor="pair-name" hint="For example the name of the person wearing it.">
          <Input id="pair-name" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <ErrorBox error={error} />
        <Button type="submit" variant="primary" className="h-12 w-full text-base" loading={busy} disabled={code.length !== 6}>
          Pair watch
        </Button>
      </form>
    </Card>
  );
}
