import { useState } from "react";
import { Pencil, Plus, Sparkles, Star, Trash2 } from "lucide-react";
import { api, type MyPersona } from "../../api";
import { Badge, Button, Card, ConfirmDialog, Empty, ErrorBox, PageHeader, Spinner, useAsync } from "../../components/ui";
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
      <PageHeader
        title="Personas"
        subtitle={<p className="text-[15px] sm:text-base">A persona sets how olá talks. Pick one for each watch in its settings.</p>}
        actions={
          <Button variant="primary" size="lg" icon={<Plus />} onClick={() => setEditing("new")}>
            New
          </Button>
        }
      />

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
                  <li key={p.id} className="rounded-(--r-card) border border-(--card-border) bg-surface shadow-(--card-shadow) p-(--card-pad)">
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
            <h2 className="mb-2 text-sm font-semibold">From olá</h2>
            <ul className="space-y-3">
              {system.map((p) => (
                <li key={p.id} className="rounded-(--r-card) border border-border bg-surface/60 p-(--card-pad)">
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
