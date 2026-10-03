import { useState } from "react";
import { ChevronDown, ChevronRight, Timer } from "lucide-react";
import type { Conversation, ConversationTurn } from "../api";
import { fmtDate, fmtDateTime, fmtMoney, fmtMs, fmtTime, KIND_LABEL, langName, langNative, UNIT_LABEL } from "../format";
import { useLanguages } from "../languages";
import { TurnStatusBadge } from "./DeviceBits";
import { Badge, Card, cx } from "./ui";

/** Chat-style rendering of a watch's conversations. `technical` adds TTFA, status, type and cost (operator);
 * `newestFirst` lists the conversations and their turns newest first (latest at the top). */
export default function ConversationList({
  conversations,
  technical,
  newestFirst,
}: {
  conversations: Conversation[];
  technical?: boolean;
  newestFirst?: boolean;
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
            </span>
          }
          bodyClassName="space-y-4"
        >
          {c.turns.length === 0 && <p className="text-sm text-muted">No turns.</p>}
          {(newestFirst ? [...c.turns].reverse() : c.turns).map((t) => (
            <div key={t.id} className="space-y-1.5">
              <div className="flex flex-col items-end">
                <div
                  className={cx(
                    "max-w-[85%] rounded-2xl rounded-br-md px-3.5 py-2 text-sm break-words",
                    t.user_text ? "bg-accent text-accent-fg" : "border border-dashed border-border text-muted italic",
                  )}
                >
                  {t.user_text || (technical ? "(nothing recognized)" : "(didn't catch that)")}
                </div>
              </div>
              {t.assistant_text && (
                <div className="flex flex-col items-start">
                  <div className="max-w-[85%] rounded-2xl rounded-bl-md bg-surface-2 px-3.5 py-2 text-sm break-words whitespace-pre-line">
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
  watch_settings: "settings changed",
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
            {t.billable != null && <> · {t.billable ? "Counted against the customer's allowance" : "Not charged to the customer"}</>}
          </p>
          {t.search && (
            <p className="mt-1">
              <span className="text-muted">Searched:</span> {t.search.split(" -> ")[0]}
              <span className="block">
                <span className="text-muted">Found:</span> {t.search.split(" -> ").slice(1).join(" -> ")}
              </span>
            </p>
          )}
          {t.error && <p className="mt-1 text-danger">Error: {t.error}</p>}
        </div>
      )}
    </div>
  );
}
