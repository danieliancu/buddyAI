import { useState } from "react";
import { AlertTriangle, Trash2 } from "lucide-react";
import { api, type DeviceIssue, type IssueKind, type IssueSeverity } from "../api";
import { useLive } from "../live";
import { fmtAgo, fmtDateTime } from "../format";
import { Badge, Button, Card, ConfirmDialog, Empty, ErrorBox, PageHeader, Select, Spinner, Table, useAsync } from "../components/ui";

const KIND_LABEL: Record<IssueKind, string> = {
  reboot: "Restart",
  disconnect: "Connection lost",
  turn_interrupted: "Turn interrupted",
  server_timeout: "Watch went silent",
};

const SEVERITY: Record<IssueSeverity, { tone: "danger" | "warn" | "neutral"; label: string }> = {
  error: { tone: "danger", label: "Problem" },
  warn: { tone: "warn", label: "Warning" },
  info: { tone: "neutral", label: "Info" },
};

const LIMIT = 300;

export default function IssuesPage() {
  const [deviceId, setDeviceId] = useState("");
  const [kind, setKind] = useState<IssueKind | "">("");
  const [problemsOnly, setProblemsOnly] = useState(false);
  const [clearing, setClearing] = useState(false);
  const devices = useAsync(api.devices.list, []);
  const issues = useAsync(
    () => api.issues.list({ deviceId: deviceId || undefined, kind: kind || undefined, problemsOnly }),
    [deviceId, kind, problemsOnly],
  );

  useLive((e) => {
    if (e.type !== "device_issue") return;
    const i = e.issue;
    if ((deviceId && i.device_id !== deviceId) || (kind && i.kind !== kind) || (problemsOnly && i.severity === "info")) return;
    const name = devices.data?.find((d) => d.id === i.device_id)?.name ?? null;
    issues.setData((l) => [{ ...i, device_name: name }, ...(l ?? [])].slice(0, LIMIT));
  });

  const list = issues.data ?? [];
  const selected = devices.data?.find((d) => d.id === deviceId);

  return (
    <>
      <PageHeader
        title="Issues"
        subtitle="Restarts and lost connections reported by the watches, newest first. Updates live."
        actions={
          <Button variant="ghost" icon={<Trash2 className="size-4" />} disabled={list.length === 0} onClick={() => setClearing(true)}>
            Clear
          </Button>
        }
      />
      <Card bodyClassName="p-0 px-4">
        <div className="flex flex-wrap items-center gap-3 border-b border-border py-3">
          <div className="w-48">
          <Select value={deviceId} onChange={(e) => setDeviceId(e.target.value)} aria-label="Watch">
            <option value="">All watches</option>
            {(devices.data ?? []).map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </Select>
          </div>
          <div className="w-48">
          <Select value={kind} onChange={(e) => setKind(e.target.value as IssueKind | "")} aria-label="Type">
            <option value="">All types</option>
            {(Object.keys(KIND_LABEL) as IssueKind[]).map((k) => (
              <option key={k} value={k}>
                {KIND_LABEL[k]}
              </option>
            ))}
          </Select>
          </div>
          <label className="flex items-center gap-2 text-sm text-muted">
            <input type="checkbox" checked={problemsOnly} onChange={(e) => setProblemsOnly(e.target.checked)} />
            Hide info (normal power-ons, updates)
          </label>
        </div>
        <ErrorBox error={issues.error} onRetry={issues.reload} />
        {issues.loading && !issues.data ? (
          <Spinner />
        ) : list.length === 0 ? (
          <Empty icon={<AlertTriangle className="size-8" />} title="No issues">
            The watches report restarts and lost connections when they reconnect.
          </Empty>
        ) : (
          <Table>
            <thead>
              <tr>
                <th>When</th>
                <th>Watch</th>
                <th>Type</th>
                <th>What happened</th>
                <th>Firmware</th>
              </tr>
            </thead>
            <tbody>
              {list.map((i) => (
                <IssueRow key={i.id} issue={i} />
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      <ConfirmDialog
        open={clearing}
        title="Clear issues"
        message={selected ? `Delete all issues of ${selected.name}?` : "Delete the issues of all watches?"}
        confirmLabel="Clear"
        danger
        onConfirm={async () => {
          await api.issues.clear(deviceId || undefined);
          issues.reload();
        }}
        onClose={() => setClearing(false)}
      />
    </>
  );
}

function IssueRow({ issue: i }: { issue: DeviceIssue }) {
  const sev = SEVERITY[i.severity] ?? SEVERITY.warn;
  return (
    <tr>
      <td className="whitespace-nowrap" title={fmtDateTime(i.created_at)}>
        {fmtDateTime(i.created_at)}
        <span className="block text-[11px] text-muted">{fmtAgo(i.created_at)}</span>
      </td>
      <td className="max-w-40 truncate" title={i.device_id}>
        {i.device_name ?? i.device_id}
      </td>
      <td className="whitespace-nowrap">
        <div className="flex items-center gap-1.5">
          <Badge tone={sev.tone}>{sev.label}</Badge>
          <span>{KIND_LABEL[i.kind] ?? i.kind}</span>
        </div>
      </td>
      <td className="max-w-xl whitespace-normal">
        {i.summary}
        {i.reason && <span className="ml-1.5 font-mono text-[11px] text-muted">{i.reason}</span>}
      </td>
      <td className="font-mono text-[11px] text-muted">{i.fw_version || "—"}</td>
    </tr>
  );
}
