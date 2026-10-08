import { useEffect, useState } from "react";
import { useParams } from "react-router";
import { MessagesSquare, Trash2 } from "lucide-react";
import { api, ApiError, type Device } from "../../api";
import { useLive } from "../../live";
import ConversationList from "../../components/ConversationList";
import WatchPreview from "../../components/WatchPreview";
import { Button, Card, ConfirmDialog, Empty, ErrorBox, Spinner, useAsync } from "../../components/ui";
import WatchHeader from "./WatchHeader";

export default function WatchHistoryPage() {
  const { id = "" } = useParams();
  const [device, setDevice] = useState<Device | null>(null);
  const [confirm, setConfirm] = useState(false);
  const convs = useAsync(() => api.me.devices.conversations(id), [id]);
  // The watch on the right, as on the Settings tab (same layout: the page keeps its width and position)
  const settings = useAsync(() => api.me.devices.settings(id), [id]);

  useEffect(() => {
    api.me.devices
      .list()
      .then((l) => setDevice(l.find((d) => d.id === id) ?? null))
      .catch(() => undefined);
  }, [id]);

  useLive((e) => {
    if (!("device_id" in e) || e.device_id !== id) return;
    if (e.type === "turn_end") convs.reload();
    else if (e.type === "device_online" || e.type === "device_offline")
      setDevice((d) => d && { ...d, online: e.type === "device_online", state: e.type === "device_online" ? "idle" : null });
    else if (e.type === "device_state") setDevice((d) => d && { ...d, state: e.state });
    else if (e.type === "device_status")
      setDevice((d) => d && { ...d, battery_pct: e.battery_pct ?? d.battery_pct, charging: e.charging ?? d.charging });
  });

  // Newest first: conversations (already sorted by the server) and the exchanges inside each one.
  const list = (convs.data ?? []).map((c) => ({ ...c, turns: [...c.turns].reverse() }));
  const count = list.reduce((n, c) => n + c.turns.length, 0);
  const notFound = convs.error instanceof ApiError && convs.error.status === 404;

  const s = settings.data?.settings;
  return (
    <>
      <WatchHeader id={id} device={device} active="history" onRenamed={(name) => setDevice((d) => d && { ...d, name })} />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
      <div className="min-w-0">
      {notFound ? (
        <ErrorBox error={new Error("Watch not found.")} />
      ) : (
        <>
          <div className="mb-4 flex items-center justify-between gap-3">
            <p className="text-sm text-muted">
              {convs.data ? `${count} ${count === 1 ? "question" : "questions"} · newest first` : " "}
            </p>
            <Button size="sm" variant="danger" icon={<Trash2 className="size-3.5" />} disabled={count === 0} onClick={() => setConfirm(true)}>
              Delete history
            </Button>
          </div>
          <ErrorBox error={convs.error} onRetry={convs.reload} />
          {convs.loading && !convs.data ? (
            <Spinner />
          ) : list.length === 0 ? (
            <Card>
              <Empty icon={<MessagesSquare className="size-8" />} title="No conversations yet">
                Press the button on the watch and ask a question — it will appear here.
              </Empty>
            </Card>
          ) : (
            <ConversationList conversations={list} />
          )}
        </>
      )}
      </div>
        <aside className="hidden lg:block" data-testid="history-watch">
          {s && (
            <div className="sticky top-8 space-y-3">
              <p className="text-center text-xs text-muted">Live preview · 410×502</p>
              <WatchPreview theme={s.theme} language={s.language} preferredLanguage={s.preferred_language}
                timezone={s.timezone} brightness={s.brightness} />
            </div>
          )}
        </aside>
      </div>

      <ConfirmDialog
        open={confirm}
        danger
        title="Delete history"
        confirmLabel="Delete permanently"
        message={
          <>
            All conversations of <b>{device?.name ?? "this watch"}</b> ({count} {count === 1 ? "question" : "questions"}) will be
            permanently deleted. The watch also forgets what was said before. This cannot be undone.
          </>
        }
        onConfirm={async () => {
          await api.me.devices.deleteHistory(id);
          convs.reload();
        }}
        onClose={() => setConfirm(false)}
      />
    </>
  );
}
