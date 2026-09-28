import { useRef, useState, type FormEvent } from "react";
import { Cpu, Send, Upload } from "lucide-react";
import { api, ApiError, type Device, type FirmwareRelease } from "../api";
import { useLive } from "../live";
import { fmtBytes, fmtDateTime } from "../format";
import { OnlineDot } from "../components/DeviceBits";
import { Badge, Button, Card, Dialog, Empty, ErrorBox, Field, Input, PageHeader, Select, Spinner, Table, Textarea, useAsync } from "../components/ui";

export default function FirmwarePage() {
  const releases = useAsync(api.firmware.list, []);
  const devices = useAsync(api.devices.list, []);
  const [ota, setOta] = useState<FirmwareRelease | null>(null);

  useLive((e) => {
    if (e.type === "device_online" || e.type === "device_offline")
      devices.setData((l) => l?.map((d) => (d.id === e.device_id ? { ...d, online: e.type === "device_online" } : d)) ?? l);
    if (e.type === "device_paired") devices.reload();
  });

  const list = releases.data ?? [];

  return (
    <>
      <PageHeader title="Firmware" subtitle="Upload .bin images and send OTA updates to online watches." />
      <div className="space-y-4">
        <UploadCard onUploaded={() => releases.reload()} />
        <Card title="Releases" bodyClassName="p-0 px-4">
          <ErrorBox error={releases.error} onRetry={releases.reload} />
          {releases.loading && !releases.data ? (
            <Spinner />
          ) : list.length === 0 ? (
            <Empty icon={<Cpu className="size-8" />} title="No releases uploaded" />
          ) : (
            <Table>
              <thead>
                <tr>
                  <th>Version</th>
                  <th>File</th>
                  <th className="text-right">Size</th>
                  <th>SHA-256</th>
                  <th>Uploaded</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {list.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <span className="font-mono font-medium">{r.version}</span>
                      {r.notes && <span className="block max-w-64 truncate text-[11px] text-muted" title={r.notes}>{r.notes}</span>}
                    </td>
                    <td className="max-w-48 truncate text-muted">
                      <a className="hover:underline" href={`/fw/${r.id}.bin`}>
                        {r.filename}
                      </a>
                    </td>
                    <td className="tabular text-right">{fmtBytes(r.size)}</td>
                    <td className="font-mono text-[11px] text-muted" title={r.sha256}>
                      {r.sha256.slice(0, 12)}…
                    </td>
                    <td className="whitespace-nowrap">{fmtDateTime(r.uploaded_at)}</td>
                    <td>
                      <Button size="sm" variant="primary" icon={<Send className="size-3.5" />} onClick={() => setOta(r)}>
                        Send OTA
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
      </div>
      <OtaDialog release={ota} devices={devices.data ?? []} onClose={() => setOta(null)} />
    </>
  );
}

function UploadCard({ onUploaded }: { onUploaded: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [version, setVersion] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [ok, setOk] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!file) return setError(new Error("Choose a .bin file."));
    setBusy(true);
    setError(null);
    setOk(null);
    try {
      const r = await api.firmware.upload(file, version.trim(), notes.trim());
      setOk(`Uploaded: version ${r.version}`);
      setFile(null);
      setVersion("");
      setNotes("");
      if (fileRef.current) fileRef.current.value = "";
      onUploaded();
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) setError(new Error("The file is not an ESP32 application image (.bin)."));
      else if (err instanceof ApiError && err.status === 413) setError(new Error("The image is larger than the OTA partition (6 MB)."));
      else setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Upload firmware">
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        <Field label=".bin file" htmlFor="fw-file" hint={file ? `${file.name} · ${fmtBytes(file.size)}` : "ESP32-S3 application image, max 6 MB"}>
          <input
            id="fw-file"
            ref={fileRef}
            type="file"
            accept=".bin,application/octet-stream"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="block w-full text-sm text-muted file:mr-3 file:h-10 file:cursor-pointer file:rounded-lg file:border file:border-border file:bg-surface-2 file:px-4 file:text-sm file:text-fg"
          />
        </Field>
        <Field label="Version (optional)" htmlFor="fw-ver" hint="Default: read from the image">
          <Input id="fw-ver" maxLength={32} placeholder="e.g. 0.2.0" value={version} onChange={(e) => setVersion(e.target.value)} />
        </Field>
        <Field label="Notes (optional)" htmlFor="fw-notes" className="sm:col-span-2">
          <Textarea id="fw-notes" rows={2} className="min-h-0" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </Field>
        <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
          <Button type="submit" variant="primary" icon={<Upload className="size-4" />} loading={busy} disabled={!file}>
            Upload
          </Button>
          {ok && <span className="text-sm text-ok">{ok}</span>}
          <div className="w-full">
            <ErrorBox error={error} />
          </div>
        </div>
      </form>
    </Card>
  );
}

function OtaDialog({ release, devices, onClose }: { release: FirmwareRelease | null; devices: Device[]; onClose: () => void }) {
  const [deviceId, setDeviceId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<string | null>(null);
  const online = devices.filter((d) => d.online);
  const selected = devices.find((d) => d.id === deviceId);

  const close = () => {
    setError(null);
    setResult(null);
    onClose();
  };

  const send = async () => {
    if (!release || !deviceId) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.devices.ota(deviceId, release.id);
      setResult(`Offer ${r.version} sent. The watch downloads and verifies the image, then reboots.`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setError(new Error("The watch is offline."));
      else setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={!!release}
      onClose={close}
      title={`Send OTA ${release?.version ?? ""}`}
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            {result ? "Close" : "Cancel"}
          </Button>
          {!result && (
            <Button variant="primary" icon={<Send className="size-4" />} loading={busy} disabled={!selected?.online} onClick={send}>
              Send
            </Button>
          )}
        </>
      }
    >
      <div className="space-y-4 text-sm">
        {online.length === 0 ? (
          <p className="text-muted">No watch is online. OTA can only be sent to a connected watch.</p>
        ) : (
          <Field label="Watch">
            <Select value={deviceId} onChange={(e) => setDeviceId(e.target.value)}>
              <option value="">Choose a watch…</option>
              {online.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} · fw {d.fw_version || "?"}
                </option>
              ))}
            </Select>
          </Field>
        )}
        {selected && (
          <div className="flex items-center gap-2 text-xs text-muted">
            <OnlineDot online={selected.online} />
            {selected.fw_version === release?.version ? (
              <Badge tone="warn">The watch already runs this version</Badge>
            ) : (
              <span>
                {selected.fw_version || "?"} → <b className="text-fg">{release?.version}</b>
              </span>
            )}
          </div>
        )}
        <ErrorBox error={error} />
        {result && <p className="text-ok">{result}</p>}
      </div>
    </Dialog>
  );
}
