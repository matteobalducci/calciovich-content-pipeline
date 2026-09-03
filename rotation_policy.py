"""
Editorial pauses, enforced where publishing actually happens.

WHY THIS EXISTS
---------------
On 2026-09-02 the author stopped the AI-generated goal clips: they were 96.5% of
the channel's views but brought an audience that never converted. The decision
was written into `rotation-state.json` (`gol_ai_paused`) and into the coach's
decision log.

Both of those are read by the daily *planning* session. Neither is read by the
publishers. So the next day a Gol-AI clip went out anyway, and five more were
already sitting on YouTube with a scheduled `publishAt`, waiting to publish
themselves on a decision that had been reversed.

A pause that only exists in a document the planner reads is not a pause. This
module puts it in the path every publisher has to walk.

    policy = RotationPolicy(OUTPUT)
    if policy.is_paused(item_category):
        skip

It fails OPEN on a missing or unreadable state file: an editorial pause is a
preference, and losing one should slow publishing down rather than stop it. That
is the opposite of the upload registry's fail-closed rule, and deliberately so —
there the failure mode is a duplicate public post, here it is a video going out
a day late.
"""

from __future__ import annotations

import json
import os

# Categories in app/data.json map onto the pause keys in rotation-state.json.
# Kept explicit rather than derived, so adding a pausable format is a visible
# edit rather than a naming coincidence.
PAUSE_KEYS = {
    "Gol-AI": "gol_ai_paused",
}

# Paths under output/ that identify a format regardless of how the item is
# labelled — a mislabelled item must not slip past a pause.
PAUSED_PATH_MARKERS = {
    "gol_ai_paused": ("ai-clips",),
}


class RotationPolicy:
    """Reads the editorial pauses that the planning session writes."""

    def __init__(self, output_dir: str, state_file: str = "rotation-state.json"):
        self.path = os.path.join(output_dir, state_file)
        self.state = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            # Fail open: see the module docstring.
            return {}

    def _reason(self, key: str) -> str | None:
        value = self.state.get(key)
        if not value:
            return None
        return str(value) if isinstance(value, str) else "in pausa"

    def is_paused(self, category: str | None = None, path: str | None = None) -> bool:
        return self.pause_reason(category, path) is not None

    def pause_reason(self, category: str | None = None,
                     path: str | None = None) -> str | None:
        """Why this item must not be published, or None if it may go out.

        Checks the declared category first, then the file path, so an item whose
        category was edited by hand is still caught by where its file lives.
        """
        key = PAUSE_KEYS.get(category or "")
        if key:
            reason = self._reason(key)
            if reason:
                return reason

        if path:
            for pause_key, markers in PAUSED_PATH_MARKERS.items():
                if any(marker in path for marker in markers):
                    reason = self._reason(pause_key)
                    if reason:
                        return reason
        return None

    def paused_keys(self) -> list:
        return [k for k in PAUSE_KEYS.values() if self._reason(k)]
