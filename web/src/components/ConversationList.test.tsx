import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Conversation } from "../api";
import ConversationList, { foldText } from "./ConversationList";

const conv: Conversation = {
  id: 1,
  started_at: "2026-10-31T10:00:00Z",
  last_activity_at: "2026-10-31T10:01:00Z",
  turns: [
    {
      id: 1, status: "completed", language: "ro", ttfa_ms: null, created_at: "2026-10-31T10:00:00Z",
      user_text: "Șterge nota cu Ștefan", assistant_text: "Am șters nota „Ședință cu Ștefan”.",
    },
  ],
} as Conversation;

describe("History search (My watch → History)", () => {
  it("ignores case and diacritics", () => {
    expect(foldText("Ședință cu ȘTEFAN")).toBe("sedinta cu stefan");
    expect(foldText("Am șters nota").includes(foldText("STERS"))).toBe(true);
  });

  it("marks every match in the question and the answer, keeping the original letters", () => {
    const { container } = render(<ConversationList conversations={[conv]} highlight="stefan" />);
    const marks = [...container.querySelectorAll("mark")].map((m) => m.textContent);
    expect(marks).toEqual(["Ștefan", "Ștefan"]);
  });

  it("says why a reply was removed, without any internal detail", () => {
    const turn = { ...conv.turns[0], assistant_text: "", redacted: true };
    const { container } = render(<ConversationList conversations={[{ ...conv, turns: [turn] }]} />);
    expect(container.textContent).toContain("Response removed because its source conversation was deleted.");
    expect(container.textContent).toContain("Șterge nota");
  });

  it("marks nothing without a search", () => {
    const { container } = render(<ConversationList conversations={[conv]} />);
    expect(container.querySelector("mark")).toBeNull();
  });
});
