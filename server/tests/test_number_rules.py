"""Notes and reminders keep numbers in digits, whoever writes them (new item, note edit, reminder edit)."""

from app.items import TOOL_DEFS
from app.notes_edit import NOTE_SYSTEM
from app.reminder_edit import REMINDER_SYSTEM


def test_item_create_text_in_digits():
    create = next(t for t in TOOL_DEFS if t["name"] == "item_create")
    assert "in digits" in create["parameters"]["properties"]["text"]["description"]


def test_edit_prompts_keep_digits():
    assert "in digits" in NOTE_SYSTEM
    assert "in digits" in REMINDER_SYSTEM
