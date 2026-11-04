import { useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, EyeOff, Timer } from "lucide-react";
import type { Conversation, ConversationTurn } from "../api";
import { fmtDate, fmtDateTime, fmtMoney, fmtMs, fmtTime, KIND_LABEL, langName, langNative, UNIT_LABEL } from "../format";
import { useLanguages } from "../languages";
import { TurnStatusBadge } from "./DeviceBits";
import { Badge, Card, cx } from "./ui";

/** Lower case, without diacritics: "Șterge" and "sterge" match. */
export const foldText = (s: string) => s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();

/** `text` with every match of `query` (folded, see foldText) marked. */
function Highlight({ text, query }: { text: string; query?: string }) {
  const q = query ? foldText(query.trim()) : "";
  if (!q) return <>{text}</>;
  // Folding can change a character's length (ș -> s once its mark is dropped): map positions character by character.
  const chars = [...text];
  const owner: number[] = [];
  chars.forEach((c, i) => {
    for (let k = 0; k < foldText(c).length; k++) owner.push(i);
  });
  const flat = chars.map(foldText).join("");
  const marked = new Array<boolean>(chars.length).fill(false);
  for (let at = flat.indexOf(q); at !== -1; at = flat.indexOf(q, at + q.length)) {
    for (let k = at; k < at + q.length; k++) marked[owner[k]] = true;
  }
  const out: ReactNode[] = [];
  for (let i = 0; i < chars.length; ) {
    let j = i;
    while (j < chars.length && marked[j] === marked[i]) j++;
    const part = chars.slice(i, j).join("");
    out.push(marked[i] ? <mark key={i} className="rounded-sm bg-yellow-300/60 text-inherit">{part}</mark> : part);
    i = j;
  }
  return <>{out}</>;
}

/** Chat-style rendering of a watch's conversations. `technical` adds TTFA, status, type and cost (operator);
 * `newestFirst` lists the conversations and their turns newest first (latest at the top); `highlight` marks the
 * words searched for; `onReveal` (principal operator) offers exceptional access to one conversation. */
export default function ConversationList({
  conversations,
  technical,
  newestFirst,
  highlight,
  onReveal,
}: {
  conversations: Conversation[];
  technical?: boolean;
  newestFirst?: boolean;
  highlight?: string;
  onReveal?: (conversation: Conversation) => void;
}) {
  const langs = useLanguages();
  return (
    <div className="space-y-4">
      {(newestFirst
        ? [...conversations].sort((a, b) => String(b.last_activity_at).localeCompare(String(a.last_activity_at)))
        : conversations
      ).map((c) => (
        <Card
          key={c.id}
          title={
            <span className="font-normal">
              <b className="font-semibold">{c.edits ? `Voice edits · ${fmtDate(c.started_at)}` : fmtDateTime(c.started_at)}</b>
              <span className="text-muted">
                {" "}
                · {c.turns.length} {technical ? "turns" : c.turns.length === 1 ? "question" : "questions"}
                {technical && <> · last activity {fmtTime(c.last_activity_at)}</>}
                {technical && c.turns.some((t) => t.usage) && <> · £{totalCost(c.turns).toFixed(2)}</>}
              </span>
              {onReveal && c.turns.some((t) => t.content_hidden) && (
                <button type="button" className="link ml-2 text-xs" onClick={() => onReveal(c)}>
                  Open with a reason
                </button>
              )}
            </span>
          }
          bodyClassName="space-y-4"
        >
          {c.turns.length === 0 && <p className="text-sm text-muted">No turns.</p>}
          {(newestFirst ? [...c.turns].reverse() : c.turns).map((t) => (
            <div key={t.id} className="space-y-1.5">
              {t.content_hidden ? (
                <p className="inline-flex items-center gap-1.5 text-xs text-muted italic">
                  <EyeOff className="size-3.5" /> Content hidden
                </p>
              ) : (
              <>
              <div className="flex flex-col items-end">
                <div
                  className={cx(
                    "max-w-[85%] rounded-2xl rounded-br-md px-3.5 py-2 text-sm break-words",
                    t.user_text ? "bg-accent text-accent-fg" : "border border-dashed border-border text-muted italic",
                  )}
                >
                  {t.user_text ? (
                    <Highlight text={t.user_text} query={highlight} />
                  ) : technical ? (
                    "(nothing recognized)"
                  ) : (
                    "(didn't catch that)"
                  )}
                </div>
              </div>
              {t.assistant_text && (
                <div className="flex flex-col items-start">
                  <div className="max-w-[85%] rounded-2xl rounded-bl-md bg-surface-2 px-3.5 py-2 text-sm break-words whitespace-pre-line">
                    <Highlight text={t.assistant_text} query={highlight} />
                  </div>
                </div>
              )}
              </>
              )}
              <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted">
                <span>{fmtTime(t.created_at)}</span>
                {t.language !== "auto" && (
                  <span title={langName(t.language, langs)}>
                    <Badge>{langNative(t.language, langs)}</Badge>
                  </span>
                )}
                {technical && <TurnStatusBadge status={t.status} />}
                {technical && t.ttfa_ms != null && (
                  <span className="inline-flex items-center gap-1" title="Time to first audio">
                    <Timer className="size-3" />
                    TTFA {t.ttfa_ms} ms
                  </span>
                )}
                {technical && t.usage && <TurnCost turn={t} />}
              </div>
            </div>
          ))}
        </Card>
      ))}
    </div>
  );
}

export function totalCost(turns: ConversationTurn[]): number {
  return turns.reduce((n, t) => n + (t.cost_gbp ?? 0), 0);
}

const TOOL_LABEL: Record<string, string> = {
  web_search: "web search",
  watch_settings: "watch settings",  // read or changed
  item_create: "created",
  item_update: "changed",
  item_delete: "deleted",
  item_list: "listed",
  item_show: "opened",
};

/** "Conversation · web search, note created" / "Note edit" */
export function turnType(t: ConversationTurn): string {
  if (t.mode === "note") return "Note edit (voice)";
  if (t.mode === "reminder") return "Reminder edit (voice)";
  if (t.mode === "edit") return "Note or reminder edit (voice)";
  const tools = (t.tools ?? []).map((x) => {
    const [name, kind] = x.split(":");
    const label = TOOL_LABEL[name] ?? name;
    return kind ? `${kind} ${label}` : label;
  });
  return ["Conversation", tools.join(", ")].filter(Boolean).join(" · ");
}

function fmtQuantity(v: number, unit: string): string {
  const n = unit === "audio_second" ? v.toLocaleString("en-GB", { maximumFractionDigits: 1 }) : Math.round(v).toLocaleString("en-GB");
  return `${n} ${UNIT_LABEL[unit] ?? unit.replace(/_/g, " ")}`;
}

/** Operator: type and cost of one turn; click for each resource used, its quantity and cost. */
function TurnCost({ turn: t }: { turn: ConversationTurn }) {
  const [open, setOpen] = useState(false);
  const usage = t.usage ?? [];
  return (
    <div className="contents">
      <span>{turnType(t)}</span>
      <div className="basis-full">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-0.5 rounded-full bg-danger px-2 py-0.5 font-medium text-white hover:opacity-90"
        aria-expanded={open}
      >
        {t.mock ? "mock" : t.cost_gbp == null ? "—" : `£${t.cost_gbp.toFixed(3)}`}
        {!!t.unpriced && <span title="Some resources have no pricing rule">*</span>}
        {open ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
        <span className="font-normal">details</span>
      </button>
      </div>
      {open && (
        <div className="basis-full rounded-lg border border-border bg-surface-2/50 px-3 py-2 text-xs text-fg">
          {usage.length === 0 ? (
            <p className="text-muted">No resources were used (nothing was charged).</p>
          ) : (
            <table className="w-full">
              <tbody className="[&_td]:py-0.5 [&_td]:pr-3">
                {usage.map((u, i) => (
                  <tr key={i}>
                    <td className="font-medium">{KIND_LABEL[u.kind] ?? u.kind}</td>
                    <td className="text-muted">{u.provider}</td>
                    <td className="font-mono text-[11px]">{u.model}</td>
                    <td className="tabular">{fmtQuantity(u.quantity, u.unit)}</td>
                    <td className="tabular text-right">{u.cost_gbp == null ? <span className="text-warn">no price</span> : fmtMoney(u.cost_gbp)}</td>
                  </tr>
                ))}
                <tr className="border-t border-border font-medium">
                  <td colSpan={4}>Total</td>
                  <td className="tabular text-right">{fmtMoney(t.cost_gbp)}</td>
                </tr>
              </tbody>
            </table>
          )}
          <p className="mt-1.5 text-muted">
            Timings: STT {fmtMs(t.stt_ms)} · LLM first token {fmtMs(t.llm_first_token_ms)} · TTS first audio {fmtMs(t.tts_first_audio_ms)}
            {t.billable != null && <> · {t.billable ? "Counted as one of the customer's interactions" : "Not charged to the customer"}</>}
          </p>
          {t.search && (
            <p className="mt-1">
              <span className="text-muted">Searched:</span> {t.search.split(" -> ")[0]}
              <span className="block">
                <span className="text-muted">Found:</span> {t.search.split(" -> ").slice(1).join(" -> ")}
              </span>
            </p>
          )}
          {t.error && (
            <p className={cx("mt-1", t.status === "aborted" ? "text-fg" : "text-danger")}>
              {t.status === "aborted" ? "Why it stopped" : "Error"}: {t.error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
