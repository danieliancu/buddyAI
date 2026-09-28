import { useEffect, useState, type FormEvent } from "react";
import { Pencil, Plus, Sparkles, Star, Trash2 } from "lucide-react";
import { api, ApiError, type Persona } from "../api";
import { Badge, Button, Card, ConfirmDialog, Dialog, Empty, ErrorBox, Field, Input, PageHeader, Spinner, Textarea, Toggle, useAsync } from "../components/ui";

type Editing = Persona | "new" | null;

export default function PersonasPage() {
  const personas = useAsync(api.personas.list, []);
  const [editing, setEditing] = useState<Editing>(null);
  const [deleting, setDeleting] = useState<Persona | null>(null);

  const list = personas.data ?? [];

  return (
    <>
      <PageHeader
        title="Personas"
        subtitle="The system prompt used by the AI. Each watch can pick one; otherwise the default persona is used."
        actions={
          <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setEditing("new")}>
            Add persona
          </Button>
        }
      />
      <ErrorBox error={personas.error} onRetry={personas.reload} />
      {personas.loading && !personas.data ? (
        <Spinner />
      ) : list.length === 0 ? (
        <Card>
          <Empty icon={<Sparkles className="size-8" />} title="No personas" />
        </Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {list.map((p) => (
            <div key={p.id} className="flex flex-col rounded-xl border border-border bg-surface">
              <div className="flex items-start gap-2 p-4 pb-2">
                <h3 className="min-w-0 flex-1 truncate font-semibold">{p.name}</h3>
                {p.is_default && (
                  <Badge tone="accent">
                    <Star className="size-3" /> Default
                  </Badge>
                )}
              </div>
              <p className="line-clamp-4 px-4 text-sm whitespace-pre-line text-muted">{p.system_prompt}</p>
              <div className="mt-auto flex gap-2 px-4 pt-3 pb-4">
                <Button size="sm" icon={<Pencil className="size-3.5" />} onClick={() => setEditing(p)}>
                  Edit
                </Button>
                {!p.is_default && (
                  <Button size="sm" variant="ghost" className="ml-auto text-danger" icon={<Trash2 className="size-3.5" />} onClick={() => setDeleting(p)}>
                    Delete
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      <PersonaDialog
        editing={editing}
        onClose={() => setEditing(null)}
        onSaved={() => personas.reload()}
      />
      <ConfirmDialog
        open={!!deleting}
        danger
        title="Delete persona"
        confirmLabel="Delete"
        message={
          <>
            Delete <b>{deleting?.name}</b>? Watches using it will switch to the default persona.
          </>
        }
        onConfirm={async () => {
          if (!deleting) return;
          try {
            await api.personas.remove(deleting.id);
          } catch (e) {
            if (e instanceof ApiError && e.status === 400) throw new Error("The default persona cannot be deleted.");
            throw e;
          }
          personas.reload();
        }}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function PersonaDialog({ editing, onClose, onSaved }: { editing: Editing; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [isDefault, setIsDefault] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const wasDefault = editing !== "new" && !!editing?.is_default;

  useEffect(() => {
    if (!editing) return;
    setError(null);
    if (editing === "new") {
      setName("");
      setPrompt("");
      setIsDefault(false);
    } else {
      setName(editing.name);
      setPrompt(editing.system_prompt);
      setIsDefault(editing.is_default);
    }
  }, [editing]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !prompt.trim()) return setError(new Error("Name and prompt are required."));
    setBusy(true);
    setError(null);
    const body = { name: name.trim(), system_prompt: prompt.trim(), is_default: isDefault };
    try {
      if (editing === "new") await api.personas.create(body);
      else if (editing) await api.personas.update(editing.id, body);
      onSaved();
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={!!editing}
      onClose={onClose}
      wide
      title={editing === "new" ? "New persona" : "Edit persona"}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="persona-form" loading={busy}>
            Save
          </Button>
        </>
      }
    >
      <form id="persona-form" onSubmit={submit} className="space-y-4">
        <Field label="Name" htmlFor="pn">
          <Input id="pn" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="System prompt" htmlFor="pp" hint={`${prompt.length}/4000 · Replies are spoken: ask for short sentences, no lists or emoji.`}>
          <Textarea id="pp" rows={9} maxLength={4000} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        </Field>
        {/* The server has no way to "unset" the default without choosing another one. */}
        <Toggle
          checked={isDefault}
          onChange={(v) => (wasDefault ? undefined : setIsDefault(v))}
          label={wasDefault ? "Default (make another persona the default to change this)" : "Use as default"}
        />
        <ErrorBox error={error} />
      </form>
    </Dialog>
  );
}
