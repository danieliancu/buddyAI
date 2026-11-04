import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { MessagesSquare, Trash2 } from "lucide-react";
import { api, type ContentAccessBody, type Conversation } from "../api";
import { useOperator } from "../operator";
import { useLive } from "../live";
import { DevicePicker } from "../components/DeviceBits";
import ConversationList, { totalCost } from "../components/ConversationList";
import { fmtMoney } from "../format";
import { Button, Card, Dialog, Empty, ErrorBox, Field, Input, PageHeader, PasswordInput, Spinner, Textarea, useAsync } from "../components/ui";

const MIN_REASON_CHARS = 20;

/** Reason, password and two-factor code for exceptional access to customer content. Every attempt is in the audit log. */
function ContentAccessDialog({
  open,
  title,
  action,
  children,
  onSubmit,
  onClose,
}: {
  open: boolean;
  title: string;
  action: string;
  children: React.ReactNode;
  onSubmit: (body: ContentAccessBody) => Promise<void>;
  onClose: () => void;
}) {
  const [body, setBody] = useState<ContentAccessBody>({ reason: "", password: "", code: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const close = () => {
    setBody({ reason: "", password: "", code: "" });
    setError(null);
    onClose();
  };
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await onSubmit(body);
      close();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open={open}
      onClose={close}
      title={title}
      footer={
        <>
          <Button onClick={close}>Cancel</Button>
          <Button variant="danger" disabled={busy || body.reason.trim().length < MIN_REASON_CHARS || !body.password || !body.code} onClick={submit}>
            {action}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-sm text-muted">{children}</p>
        <Field label="Reason">
          <Textarea rows={3} maxLength={500} value={body.reason} onChange={(e) => setBody({ ...body, reason: e.target.value })} />
        </Field>
        <Field label="Your password">
          <PasswordInput autoComplete="current-password" value={body.password} onChange={(e) => setBody({ ...body, password: e.target.value })} />
        </Field>
        <Field label="Two-factor code">
          <Input autoComplete="one-time-code" inputMode="numeric" value={body.code} onChange={(e) => setBody({ ...body, code: e.target.value })} />
        </Field>
        <ErrorBox error={error} />
      </div>
    </Dialog>
  );
}

export default function ConversationsPage() {
  const [params, setParams] = useSearchParams();
  const devices = useAsync(api.devices.list, []);
  const deviceId = params.get("device") ?? "";
  const [confirm, setConfirm] = useState(false);
  const [revealing, setRevealing] = useState<Conversation | null>(null);
  const [revealed, setRevealed] = useState<Record<number, Conversation>>({});
  const principal = useOperator().me?.role === "principal";

  // Default to the first device.
  useEffect(() => {
    if (!deviceId && devices.data?.length) setParams({ device: devices.data[0].id }, { replace: true });
  }, [deviceId, devices.data, setParams]);

  const convs = useAsync(() => (deviceId ? api.devices.conversations(deviceId) : Promise.resolve([])), [deviceId]);

  useLive((e) => {
    if (e.type === "turn_end" && e.device_id === deviceId) convs.reload();
  });

  const device = devices.data?.find((d) => d.id === deviceId);
  // Opened conversations stay on this screen only until the watch or the page changes.
  useEffect(() => setRevealed({}), [deviceId]);
  const list = (convs.data ?? []).map((c) => revealed[c.id] ?? c);
  const turnCount = list.reduce((n, c) => n + c.turns.length, 0);
  const chats = list.filter((c) => !c.edits).length;
  const cost = list.reduce((n, c) => n + totalCost(c.turns), 0);

  return (
    <>
      <PageHeader
        title="Conversations"
        subtitle={
          device ? `${chats} ${chats === 1 ? "conversation" : "conversations"} • ${turnCount} turns • ${fmtMoney(cost)} total` : undefined
        }
        actions={
          <div className="flex items-center gap-2">
            {devices.data && devices.data.length > 0 && (
              <div className="w-56">
                <DevicePicker devices={devices.data} value={deviceId} onChange={(id) => setParams({ device: id })} />
              </div>
            )}
            {principal && (
              <Button variant="danger" icon={<Trash2 className="size-4" />} disabled={!deviceId || turnCount === 0} onClick={() => setConfirm(true)}>
                Delete history
              </Button>
            )}
          </div>
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
        <ConversationList conversations={list} technical newestFirst onReveal={principal ? setRevealing : undefined} />
      )}

      <ContentAccessDialog
        open={revealing !== null}
        title="Open this conversation"
        action="Open conversation"
        onSubmit={async (body) => {
          const c = await api.devices.revealConversation(deviceId, revealing!.id, body);
          setRevealed((r) => ({ ...r, [c.id]: c }));
        }}
        onClose={() => setRevealing(null)}
      >
        Only for a justified reason, such as a customer's request or a security incident. Your reason and this access are recorded in the audit log.
      </ContentAccessDialog>

      <ContentAccessDialog
        open={confirm}
        title="Delete history"
        action="Delete permanently"
        onSubmit={async (body) => {
          await api.devices.deleteHistory(deviceId, body);
          setRevealed({});
          convs.reload();
        }}
        onClose={() => setConfirm(false)}
      >
        All conversations of {device?.name} ({turnCount} turns) will be permanently deleted. Usage and cost records are kept, without content. This cannot be undone.
      </ContentAccessDialog>
    </>
  );
}
