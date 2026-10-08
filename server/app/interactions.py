"""What counts as one of the customer's AI interactions (ola Care: 1,000 per billing period).

One valid user request = one interaction, however many AI calls, tools or web searches it used internally.
The decision is made once, when the operation is settled (usage_operations.interaction):

  counts         a chat / note / reminder request that was understood (a transcript exists) and either
                 completed, or was cancelled by the user (tap) after processing had begun
  never counts   silence (no_speech); in a note / reminder edit screen (mic left open), speech that was not an
                 instruction for the item (background talk, "ignored") or that was about another item ("other");
                 failures on ola's side (error, start_failed, timeout of the answer,
                 lost usage lease, server shutdown, expired operations); a dropped connection; a watch-side
                 error abort; refused or duplicate requests; background work (memory learning, embeddings);
                 voice samples and operator tests
"""

from __future__ import annotations

INTERACTION_KINDS = ("chat", "note", "reminder")


NOT_A_REQUEST = ("ignored", "other")  # edit-mode outcomes (app/pipeline/conversation.py _edit_no_call)


def counts_as_interaction(kind: str, status: str, abort_reason: str | None, user_text: str | None,
                          edit_outcome: str = "") -> bool:
    if kind not in INTERACTION_KINDS:
        return False
    understood = bool((user_text or "").strip())
    if not understood:
        return False  # silence, or nothing was transcribed before it ended
    if edit_outcome in NOT_A_REQUEST:
        return False  # the open edit mic heard talk that was not meant for the item
    if status == "completed":
        return True
    return status == "aborted" and abort_reason == "user_tap"
