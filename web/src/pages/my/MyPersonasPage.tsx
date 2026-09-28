import { useState } from "react";
import { Pencil, Plus, Sparkles, Star, Trash2 } from "lucide-react";
import { api, type MyPersona } from "../../api";
import { Badge, Button, Card, ConfirmDialog, Empty, ErrorBox, Spinner, useAsync } from "../../components/ui";
import { PersonaDialog, type Editing } from "../PersonasPage";

export default function MyPersonasPage() {
  const personas = useAsync(api.me.personas.list, []);
  const [editing, setEditing] = useState<Editing>(null);
  const [deleting, setDeleting] = useState<MyPersona | null>(null);

  const list = personas.data ?? [];
  const own = list.filter((p) => p.own);
  const system = list.filter((p) => !p.own);

  return (
    <div className="mx-auto max-w-2xl">
      <div className="mb-5 flex items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Personas</h1>
          <p className="mt-1 text-sm text-muted">A persona sets how Buddy talks. Pick one for each watch in its settings.</p>
        </div>
        <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setEditing("new")}>
          New
        </Button>
      </div>

      <ErrorBox error={personas.error} onRetry={personas.reload} />
      {personas.loading && !personas.data ? (
        <Spinner />
      ) : (
        <div className="space-y-6">
          <section>
            <h2 className="mb-2 text-sm font-semibold">My personas</h2>
            {own.length === 0 ? (
              <Card>
                <Empty icon={<Sparkles className="size-7" />} title="No personas of your own yet">
                  For example: “You are a patient teacher. Explain things simply and ask a question back.”
                </Empty>
              </Card>
            ) : (
              <ul className="space-y-3">
                {own.map((p) => (
                  <li key={p.id} className="rounded-xl border border-border bg-surface p-4">
                    <div className="flex items-start gap-2">
                      <h3 className="min-w-0 flex-1 truncate font-semibold">{p.name}</h3>
                      <button
                        className="rounded p-1.5 text-muted hover:bg-surface-2 hover:text-fg"
                        onClick={() => setEditing(p)}
                        aria-label={`Edit ${p.name}`}
                      >
                        <Pencil className="size-4" />
                      </button>
                      <button
                        className="rounded p-1.5 text-muted hover:bg-danger-bg hover:text-danger"
                        onClick={() => setDeleting(p)}
                        aria-label={`Delete ${p.name}`}
                      >
                        <Trash2 className="size-4" />
                      </button>
                    </div>
                    <p className="mt-1 line-clamp-3 text-sm whitespace-pre-line text-muted">{p.system_prompt}</p>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section>
            <h2 className="mb-2 text-sm font-semibold">From BuddyAI</h2>
            <ul className="space-y-3">
              {system.map((p) => (
                <li key={p.id} className="rounded-xl border border-border bg-surface/60 p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="min-w-0 flex-1 truncate font-semibold">{p.name}</h3>
                    {p.is_default && (
                      <Badge tone="accent">
                        <Star className="size-3" /> Default
                      </Badge>
                    )}
                  </div>
                  <p className="mt-1 line-clamp-3 text-sm whitespace-pre-line text-muted">{p.system_prompt}</p>
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}

      <PersonaDialog
        editing={editing}
        onClose={() => setEditing(null)}
        onSave={async (body) => {
          const input = { name: body.name, system_prompt: body.system_prompt };
          if (editing === "new") await api.me.personas.create(input);
          else if (editing) await api.me.personas.update(editing.id, input);
          personas.reload();
        }}
      />
      <ConfirmDialog
        open={!!deleting}
        danger
        title="Delete persona"
        confirmLabel="Delete"
        message={
          <>
            Delete <b>{deleting?.name}</b>? Watches using it switch to the default persona.
          </>
        }
        onConfirm={async () => {
          if (!deleting) return;
          await api.me.personas.remove(deleting.id);
          personas.reload();
        }}
        onClose={() => setDeleting(null)}
      />
    </div>
  );
}
