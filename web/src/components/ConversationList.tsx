import { Timer } from "lucide-react";
import type { Conversation } from "../api";
import { fmtDateTime, fmtTime, langName, langNative } from "../format";
import { useLanguages } from "../languages";
import { TurnStatusBadge } from "./DeviceBits";
import { Badge, Card, cx } from "./ui";

/** Chat-style rendering of a watch's conversations. `technical` adds TTFA and status badges (operator). */
export default function ConversationList({ conversations, technical }: { conversations: Conversation[]; technical?: boolean }) {
  const langs = useLanguages();
  return (
    <div className="space-y-4">
      {conversations.map((c) => (
        <Card
          key={c.id}
          title={
            <span className="font-normal">
              <b className="font-semibold">{fmtDateTime(c.started_at)}</b>
              <span className="text-muted">
                {" "}
                · {c.turns.length} {technical ? "turns" : c.turns.length === 1 ? "question" : "questions"}
                {technical && <> · last activity {fmtTime(c.last_activity_at)}</>}
              </span>
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
              </div>
            </div>
          ))}
        </Card>
      ))}
    </div>
  );
}
