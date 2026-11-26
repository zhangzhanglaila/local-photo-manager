"""Control flow must not depend on the text a widget happens to display.

Three places used to branch on prose: the status bar auto-expired a message when
it contained ``完成``, the search panel picked "model loading" when the message
mentioned ``模型`` or ``索引``, and the face menu dispatched on
``chosen.text() == "Delete"``. Every one of them silently broke as soon as the
label was translated or rephrased. The payloads now carry ids/stages/flags
instead, and this test keeps the old patterns from creeping back.
"""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "iPhoto"

FORBIDDEN = (
    '"完成" in',
    '"索引" in message',
    '"模型" in message',
    "chosen.text() ==",
)


def test_source_does_not_branch_on_rendered_text() -> None:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN:
            if pattern in text:
                offenders.append(f"{path.relative_to(SRC.parent.parent)}: {pattern}")

    assert offenders == [], (
        "control flow on rendered text (use action ids / stages / flags):\n"
        + "\n".join(offenders)
    )
