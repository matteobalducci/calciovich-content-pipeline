"""Structural check: every publishing path goes through the protocol.

WHY THIS TEST EXISTS
--------------------
The previous audit landed a fair hit: the unit tests all passed while
`carica_instagram.py --photo` still bypassed the state machine entirely and
could republish a photo after a crash. Tests over the protocol in isolation
cannot catch a caller that simply does not use it.

Testing the real publishers end-to-end would mean faking three platform SDKs and
their auth. This is the cheap 80%: it reads the source and asserts that anything
which performs a publish is wrapped in a PublishAttempt. It is a lint, not a
proof — but it is exactly the lint that would have caught that bug.
"""

import ast
import os
import sys

import pytest

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE)

PUBLISHERS = ["carica_youtube.py", "carica_instagram.py", "carica_tiktok.py"]

# Calls that create something publicly visible. Every one of them must sit
# inside a `with PublishAttempt(...)` block.
PUBLISHING_CALLS = {
    "publish_container",   # Instagram: makes the reel/photo live
    "upload_one",          # YouTube: uploads the video
    "wait_publish_complete",  # TikTok: finalises the post or inbox draft
}


def _parse(name):
    with open(os.path.join(ENGINE, name), encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=name)


def _calls_inside_publish_attempt(tree):
    """Every publishing call, tagged with whether a PublishAttempt encloses it."""
    found = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.depth = 0

        def visit_With(self, node):
            opens_attempt = any(
                isinstance(item.context_expr, ast.Call)
                and getattr(item.context_expr.func, "id", None) == "PublishAttempt"
                for item in node.items
            )
            self.depth += 1 if opens_attempt else 0
            self.generic_visit(node)
            self.depth -= 1 if opens_attempt else 0

        def visit_Call(self, node):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in PUBLISHING_CALLS:
                found.append((name, node.lineno, self.depth > 0))
            self.generic_visit(node)

    Visitor().visit(tree)
    return found


@pytest.mark.parametrize("publisher", PUBLISHERS)
def test_every_publishing_call_is_wrapped_in_a_publish_attempt(publisher):
    calls = _calls_inside_publish_attempt(_parse(publisher))
    assert calls, f"{publisher}: nessuna chiamata di pubblicazione trovata — la lista PUBLISHING_CALLS e' da aggiornare?"
    unguarded = [(n, line) for n, line, guarded in calls if not guarded]
    assert not unguarded, (
        f"{publisher}: pubblicazione fuori da PublishAttempt alle righe "
        f"{[l for _, l in unguarded]} — un crash li' puo' ripubblicare il contenuto"
    )


@pytest.mark.parametrize("publisher", PUBLISHERS)
def test_publishers_do_not_write_the_registry_by_hand(publisher):
    """Il registro si tocca solo attraverso il protocollo o la riconciliazione.

    Una begin()/confirm() sparsa nel codice e' il modo in cui la macchina a
    stati torna a divergere fra le tre piattaforme.
    """
    tree = _parse(publisher)
    diretti = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", None) in {"begin", "confirm", "fail"}
        and getattr(getattr(node.func, "value", None), "id", None) == "registry"
    ]
    assert not diretti, (
        f"{publisher}: transizioni di stato scritte a mano alle righe {diretti} — "
        f"usa PublishAttempt cosi' la classificazione degli errori resta una sola"
    )
