import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { MessagesSquare, Timer, Trash2 } from "lucide-react";
import { api } from "../api";
import { useLive } from "../live";
import { fmtDateTime, fmtTime, langName, langNative } from "../format";
import { useLanguages } from "../languages";
import { DevicePicker, TurnStatusBadge } from "../components/DeviceBits";
import { Badge, Button, Card, ConfirmDialog, Empty, ErrorBox, PageHeader, Spinner, cx, useAsync } from "../components/ui";

export default function ConversationsPage() {
  const [params, setParams] = useSearchParams();
  const devices = useAsync(api.devices.list, []);
  const deviceId = params.get("device") ?? "";
  const [confirm, setConfirm] = useState(false);
  const langs = useLanguages();

  // Default to the first device.
  useEffect(() => {
    if (!deviceId && devices.data?.length) setParams({ device: devices.data[0].id }, { replace: true });
  }, [deviceId, devices.data, setParams]);

  const convs = useAsync(() => (deviceId ? api.devices.conversations(deviceId) : Promise.resolve([])), [deviceId]);

  useLive((e) => {
    if (e.type === "turn_end" && e.device_id === deviceId) convs.reload();
  });

  const device = devices.data?.find((d) => d.id === deviceId);
  const list = convs.data ?? [];
  const turnCount = list.reduce((n, c) => n + c.turns.length, 0);

  return (
    <>
      <PageHeader
        title="Conversations"
        subtitle={device ? `${list.length} ${list.length === 1 ? "conversation" : "conversations"} • ${turnCount} turns` : undefined}
        actions={
          <>
            {devices.data && devices.data.length > 0 && (
              <DevicePicker devices={devices.data} value={deviceId} onChange={(id) => setParams({ device: id })} />
            )}
            <Button variant="danger" icon={<Trash2 className="size-4" />} disabled={!deviceId || turnCount === 0} onClick={() => setConfirm(true)}>
              Delete history
            </Button>
          </>
        }
      />
      <ErrorBox error={devices.error ?? convs.error} />
      {devices.data?.length === 0 ? (
        <Card>
          <Empty icon={<MessagesSquare className="size-8" />} title="No paired watches" />
        </Card>
      ) : convs.loading && !convs.data ? (
        <Spinner />
      ) : list.length === 0 ? (
        <Card>
          <Empty icon={<MessagesSquare className="size-8" />} title="No conversations">
            Conversations appear here once the watch is used.
          </Empty>
        </Card>
      ) : (
        <div className="space-y-4">
          {list.map((c) => (
            <Card
              key={c.id}
              title={
                <span className="font-normal">
                  <b className="font-semibold">{fmtDateTime(c.started_at)}</b>
                  <span className="text-muted"> · {c.turns.length} turns · last activity {fmtTime(c.last_activity_at)}</span>
                </span>
              }
              bodyClassName="space-y-4"
            >
              {c.turns.length === 0 && <p className="text-sm text-muted">No turns.</p>}
              {c.turns.map((t) => (
                <div key={t.id} className="space-y-1.5">
                  <div className="flex flex-col items-end">
                    <div
                      className={cx(
                        "max-w-[85%] rounded-2xl rounded-br-md px-3.5 py-2 text-sm",
                        t.user_text ? "bg-accent text-accent-fg" : "border border-dashed border-border text-muted italic",
                      )}
                    >
                      {t.user_text || "(nothing recognized)"}
                    </div>
                  </div>
                  {t.assistant_text && (
                    <div className="flex flex-col items-start">
                      <div className="max-w-[85%] rounded-2xl rounded-bl-md bg-surface-2 px-3.5 py-2 text-sm whitespace-pre-line">
                        {t.assistant_text}
                      </div>
                    </div>
                  )}
                  <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted">
                    <span>{fmtTime(t.created_at)}</span>
                    {t.language !== "auto" && (
                      <span title={langName(t.language, langs)}>
                        <Badge>{langNative(t.language, langs)}</Badge>
                      </span>
                    )}
                    <TurnStatusBadge status={t.status} />
                    {t.ttfa_ms != null && (
                      <span className="inline-flex items-center gap-1" title="Time to first audio">
                        <Timer className="size-3" />
                        TTFA {t.ttfa_ms} ms
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </Card>
          ))}
        </div>
      )}

      <ConfirmDialog
        open={confirm}
        danger
        title="Delete history"
        confirmLabel="Delete permanently"
        message={
          <>
            All conversations of <b>{device?.name}</b> ({turnCount} turns) will be permanently deleted. Usage/cost records are kept,
            without content. This cannot be undone.
          </>
        }
        onConfirm={async () => {
          await api.devices.deleteHistory(deviceId);
          convs.reload();
        }}
        onClose={() => setConfirm(false)}
      />
    </>
  );
}
