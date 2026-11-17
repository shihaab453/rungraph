"""Small Markdown escaping helpers shared by the text reports."""

from __future__ import annotations


def md_text(value: object, limit: int = 400) -> str:
    text = "".join(
        character if character.isprintable() else f"\\x{ord(character):02x}"
        for character in str(value)
    )
    for character in ("\\", "|", "`", "[", "]", "<", ">"):
        text = text.replace(character, "\\" + character)
    text = text.replace("\r", " ").replace("\n", " ")
    if len(text) > limit:
        text = text[:limit] + "..."
    return text
