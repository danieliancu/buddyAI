import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { MessagesSquare, Trash2 } from "lucide-react";
import { api } from "../api";
import { useLive } from "../live";
import { DevicePicker } from "../components/DeviceBits";
import ConversationList from "../components/ConversationList";
import { Button, Card, ConfirmDialog, Empty, ErrorBox, PageHeader, Spinner, useAsync } from "../components/ui";

export default function ConversationsPage() {
  const [params, setParams] = useSearchParams();
  const devices = useAsync(api.devices.list, []);
  const deviceId = params.get("device") ?? "";
  const [confirm, setConfirm] = useState(false);

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
        <ConversationList conversations={list} technical />
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
