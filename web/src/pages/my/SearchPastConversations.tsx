import { useState } from "react";
import { History } from "lucide-react";
import { api, type ConversationSearchChoice, type Wearer } from "../../api";
import { fmtDate } from "../../format";
import { Button, Card, ErrorBox, Toggle, useAsync } from "../../components/ui";

/**
 * Per watch: who wears it, and whether olá may search its earlier conversations. Saved at once and apart from the
 * watch settings (the server keeps it away from them: it never reaches the watch). Hidden while the feature is not
 * available for the account. A watch someone else wears stays off: the account holder cannot agree for the wearer.
 */
export default function SearchPastConversations({ id }: { id: string }) {
  const state = useAsync(() => api.me.devices.conversationSearch(id), [id]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState<ConversationSearchChoice | null>(null);
  const d = saved ?? state.data;
  if (!d || !d.available) return null;

  const choose = async (wearer: Wearer, enabled: boolean) => {
    setSaving(true);
    setError(null);
    try {
      setSaved(await api.me.devices.setConversationSearch(id, wearer, enabled));
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card title="Search Past Conversations" icon={<History />}>
      <div className="space-y-4">
        <div>
          <p className="text-sm font-medium">Who uses this watch?</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" variant={d.wearer === "self" ? "primary" : "secondary"} aria-pressed={d.wearer === "self"}
              disabled={saving} onClick={() => void choose("self", d.wearer === "self" && d.enabled)}>
              I wear this watch
            </Button>
            <Button size="sm" variant={d.wearer === "other" ? "primary" : "secondary"} aria-pressed={d.wearer === "other"}
              disabled={saving} onClick={() => void choose("other", false)}>
              Someone else wears this watch
            </Button>
          </div>
        </div>

        {d.wearer === "self" && (
          <div>
            <Toggle checked={d.enabled} onChange={(v) => void choose("self", v)}
              label={<span className="font-medium">Allow olá to search conversations held on this watch</span>} />
            <p className="mt-1 pl-14 text-xs text-muted">
              When you ask about something discussed earlier, olá looks through this watch’s conversations. To make that
              possible, their text is sent to our AI provider (OpenAI, which may process it outside the UK) to create
              searchable representations, stored with your account. Only this watch can search them.
            </p>
            <p className="mt-1 pl-14 text-xs text-muted">
              {d.enabled
                ? `On since ${d.authorized_at ? fmtDate(d.authorized_at) : "today"}. Switching off stops searching at once and deletes the searchable representations; your conversation history stays until you delete it.`
                : "Off: nothing is processed for searching."}
            </p>
          </div>
        )}

        {d.wearer === "other" && (
          <p className="rounded-xl border border-border bg-surface-2/50 px-3 py-2 text-xs text-muted" data-testid="wearer-unavailable">
            Not available yet for a watch someone else wears. These are that person’s private conversations, and we do not
            yet have an approved way for them to agree to this, so nothing is processed for searching.
          </p>
        )}

        {d.wearer === null && <p className="text-xs text-muted">Choose who uses this watch to see this option.</p>}
        <ErrorBox error={error} />
      </div>
    </Card>
  );
}
