import { useRef, useState, type FormEvent } from "react";
import { Cpu, Rocket, Send, Upload } from "lucide-react";
import { api, ApiError, type Device, type FirmwareRelease, type SignedTestResults } from "../api";
import { useLive } from "../live";
import { fmtBytes, fmtDateTime } from "../format";
import { OnlineDot } from "../components/DeviceBits";
import { Badge, Button, Card, Dialog, Empty, ErrorBox, Field, Input, PageHeader, Select, Spinner, Table, Textarea, Toggle, useAsync } from "../components/ui";

const STAGE_LABEL: Record<string, string> = {
  development: "Development",
  pilot: "Pilot",
  gradual_10: "Gradual 10%",
  gradual_25: "Gradual 25%",
  gradual_50: "Gradual 50%",
  general: "General 100%",
};
const STATE_LABEL: Record<string, string> = {
  offered: "Offered",
  accepted: "Accepted",
  downloading: "Downloading",
  installing: "Installing",
  rebooting: "Rebooting",
  verifying: "Verifying",
  installed: "Installed",
  failed: "Failed",
  rolled_back: "Rolled back",
  unknown: "Unknown",
};

export default function FirmwarePage() {
  const releases = useAsync(api.firmware.list, []);
  const devices = useAsync(api.devices.list, []);
  const [ota, setOta] = useState<FirmwareRelease | null>(null);
  const [rolling, setRolling] = useState<FirmwareRelease | null>(null);

  useLive((e) => {
    if (e.type === "device_online" || e.type === "device_offline")
      devices.setData((l) => l?.map((d) => (d.id === e.device_id ? { ...d, online: e.type === "device_online" } : d)) ?? l);
    if (e.type === "device_paired") devices.reload();
    if (e.type === "ota_paused") releases.reload();
  });

  const list = releases.data ?? [];

  return (
    <>
      <PageHeader title="Firmware" subtitle="Upload .bin images, send an update to one watch, or roll a release out stage by stage." />
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
                  <th>Rollout</th>
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
                    <td className="whitespace-nowrap">
                      <Badge tone={r.paused ? "danger" : r.stage === "development" ? "neutral" : "accent"}>
                        {r.paused ? "Paused" : STAGE_LABEL[r.stage] ?? r.stage}
                      </Badge>
                      <span className="block text-[11px] text-muted">{r.channel}{r.hw_model ? ` · ${r.hw_model}` : ""}</span>
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
                    <td className="space-x-2 whitespace-nowrap">
                      <Button size="sm" icon={<Rocket className="size-3.5" />} onClick={() => setRolling(r)}>
                        Rollout
                      </Button>
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
      <RolloutDialog release={rolling} onClose={() => { setRolling(null); releases.reload(); }} />
    </>
  );
}

function UploadCard({ onUploaded }: { onUploaded: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [version, setVersion] = useState("");
  const [notes, setNotes] = useState("");
  const [hwModel, setHwModel] = useState("");
  const [channel, setChannel] = useState("dev");
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
      const r = await api.firmware.upload(file, version.trim(), notes.trim(), hwModel.trim(), channel);
      setOk(`Uploaded: version ${r.version}`);
      setFile(null);
      setVersion("");
      setNotes("");
      if (fileRef.current) fileRef.current.value = "";
      onUploaded();
    } catch (err) {
      if (err instanceof ApiError && err.status === 413) setError(new Error("The image is larger than the OTA partition (6 MB)."));
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
        <Field label="Watch model" htmlFor="fw-model" hint="As the watches report it (see Watches). Needed for a rollout.">
          <Input id="fw-model" maxLength={64} value={hwModel} onChange={(e) => setHwModel(e.target.value)} />
        </Field>
        <Field label="Channel" htmlFor="fw-channel" hint="Release images must carry a valid signature; they go only to release builds.">
          <Select id="fw-channel" value={channel} onChange={(e) => setChannel(e.target.value)}>
            <option value="dev">Development</option>
            <option value="release">Release</option>
          </Select>
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

function RolloutDialog({ release, onClose }: { release: FirmwareRelease | null; onClose: () => void }) {
  const id = release?.id;
  const state = useAsync(() => (id ? api.firmware.rollout(id) : Promise.resolve(null)), [id]);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<SignedTestResults>({
    unit_mac: "",
    valid_image_accepted: false,
    unsigned_image_refused: false,
    foreign_key_refused: false,
    modified_image_refused: false,
  });
  const s = state.data;

  useLive((e) => {
    if (e.type === "ota_paused" && e.release_id === id) state.reload();
  });

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      state.reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const failures = s?.watches.filter((w) => w.counts_as_failure && !w.reviewed) ?? [];
  const needsEvidence = release?.channel === "release" && s && !s.signing_evidence;
  const allPassed = test.valid_image_accepted && test.unsigned_image_refused && test.foreign_key_refused && test.modified_image_refused;

  return (
    <Dialog open={release !== null} onClose={onClose} title={`Rollout of ${release?.version ?? ""}`} wide>
      <div className="space-y-4 text-sm">
        <ErrorBox error={error ?? state.error} onRetry={state.error ? state.reload : undefined} />
        {!s ? (
          <Spinner />
        ) : (
          <>
            {!s.auto_enabled && (
              <p className="rounded-lg border border-border p-3 text-muted">
                Automatic updates are off on this server (<code className="font-mono">BUDDYAI_OTA_AUTO_ENABLED</code>). Stages can be set, but nothing is offered.
              </p>
            )}
            {s.paused && <p className="rounded-lg border border-danger p-3 text-danger">Paused: {s.paused_reason}</p>}
            <p>
              Stage: <b>{STAGE_LABEL[s.stage] ?? s.stage}</b>. This stage reaches <b>{s.in_stage}</b> of {s.fleet} watches of this model and channel.
              {s.stage !== "development" && s.in_stage === 0 && " No watch is in it yet: a small fleet can have none at 10%."}
            </p>
            <div className="flex flex-wrap gap-2">
              {Object.entries(STATE_LABEL).map(([key, label]) =>
                s.tally[key] ? <Badge key={key} tone={key === "installed" ? "ok" : key === "failed" || key === "rolled_back" ? "danger" : "neutral"}>{label}: {s.tally[key]}</Badge> : null,
              )}
              {Object.keys(STATE_LABEL).every((k) => !s.tally[k]) && <span className="text-muted">No attempts yet.</span>}
            </div>

            {failures.length > 0 && (
              <div>
                <h3 className="font-semibold">Failures to review</h3>
                <ul className="mt-1 space-y-1">
                  {failures.map((w) => (
                    <li key={w.attempt_id} className="flex items-center justify-between gap-2">
                      <span className="min-w-0 break-words">
                        <code className="font-mono text-xs">{w.device_id}</code> · {STATE_LABEL[w.state] ?? w.state} · {w.reason || "no reason given"}
                      </span>
                      <Button size="sm" disabled={busy} onClick={() => act(() => api.firmware.review(w.attempt_id))}>
                        Reviewed
                      </Button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {s.watches.length > 0 && (
              <details>
                <summary className="cursor-pointer text-accent">Watches ({s.watches.length})</summary>
                <ul className="mt-1 space-y-1 text-xs">
                  {s.watches.map((w) => (
                    <li key={w.attempt_id} className="break-words">
                      <code className="font-mono">{w.device_id}</code>: {STATE_LABEL[w.state] ?? w.state}
                      {w.reason && ` (${w.reason})`} · from {w.previous_version || "?"} · {fmtDateTime(w.updated_at)}
                    </li>
                  ))}
                </ul>
              </details>
            )}

            {s.pilot_devices.length > 0 && (
              <details>
                <summary className="cursor-pointer text-accent">Pilot watches</summary>
                <ul className="mt-2 space-y-2">
                  {s.pilot_devices.map((d) => (
                    <li key={d.id} className="flex items-center justify-between gap-2">
                      <span className="min-w-0 break-words">{d.name} · {d.fw_version || "?"}</span>
                      <Toggle checked={d.pilot} label={`Pilot: ${d.name}`} onChange={(v) => act(() => api.firmware.setPilot(d.id, v))} />
                    </li>
                  ))}
                </ul>
              </details>
            )}

            {needsEvidence && (
              <div className="space-y-2 rounded-lg border border-border p-3">
                <h3 className="font-semibold">Signed-update tests on a real watch</h3>
                <p className="text-muted">Required before this release can leave the development stage (firmware/README.md).</p>
                <Field label="Watch MAC address">
                  <Input value={test.unit_mac} onChange={(e) => setTest({ ...test, unit_mac: e.target.value })} />
                </Field>
                {(
                  [
                    ["valid_image_accepted", "A correctly signed image was accepted"],
                    ["unsigned_image_refused", "An unsigned image was refused"],
                    ["foreign_key_refused", "An image signed with another key was refused"],
                    ["modified_image_refused", "An image changed after signing was refused"],
                  ] as const
                ).map(([key, label]) => (
                  <label key={key} className="flex items-center gap-2">
                    <input type="checkbox" checked={test[key]} onChange={(e) => setTest({ ...test, [key]: e.target.checked })} />
                    {label}
                  </label>
                ))}
                <Button disabled={busy || !allPassed || test.unit_mac.trim().length < 6} onClick={() => act(() => api.firmware.signedTest(id!, test))}>
                  Record the results
                </Button>
              </div>
            )}

            <div className="flex flex-wrap justify-end gap-2">
              {s.paused ? (
                <Button disabled={busy} onClick={() => act(() => api.firmware.resume(id!))}>Resume</Button>
              ) : (
                s.stage !== "development" && <Button disabled={busy} onClick={() => act(() => api.firmware.pause(id!))}>Pause</Button>
              )}
              {s.next_stage && (
                <Button variant="primary" disabled={busy || s.paused} onClick={() => act(() => api.firmware.promote(id!, s.next_stage!))}>
                  Approve: move to {STAGE_LABEL[s.next_stage] ?? s.next_stage}
                </Button>
              )}
            </div>
          </>
        )}
      </div>
    </Dialog>
  );
}
