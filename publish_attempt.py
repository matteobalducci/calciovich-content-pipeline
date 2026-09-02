"""
The publish protocol, extracted so it can be tested without a platform.

WHY THIS EXISTS
---------------
The three publishers each implemented the same write-ahead protocol by hand:
begin, call the platform, confirm — and each got a slightly different answer to
the question that actually matters, which is *what to do when a step raises*.

That difference is not cosmetic. Marking a failed attempt `failed` frees the item
for a retry; leaving it `pending` blocks it. Choose wrong in one direction and a
timeout on a successful publish becomes a duplicate public post. Choose wrong in
the other and a rejected request blocks an item forever. Getting it right in one
place, once, is the whole point.

It also gives the publishers a seam. Everything below is pure control flow over
callables, so a test can drive the exact sequence — died after creating the
container, died before the API was ever called, the platform said no — without
touching YouTube, Instagram or TikTok.

USE
---
    with PublishAttempt(registry, key, source_id=item_id, title=title) as attempt:
        container = create_container(...)
        attempt.record(containerId=container)   # persisted immediately
        media_id = publish(container)
        attempt.succeeded(media_id, url=...)

If the block raises, the attempt classifies the error and leaves the registry in
the state that makes the next run behave correctly.
"""

from __future__ import annotations


class KnownFailure(Exception):
    """The platform definitively rejected the request.

    Raise (or wrap into) this only when the external effect provably did NOT
    happen: a validation error, a 4xx that names the problem, an exhausted quota
    refusing the call. The item is then marked failed and becomes eligible again.

    Anything else — a timeout, a dropped connection, an unparsable response — is
    an UNKNOWN outcome and must not be treated as a failure, because it may sit
    on the far side of a successful publish.
    """


class AlreadySettled(Exception):
    """Another run already took this item — skip it, do not treat it as an error."""


class PublishAttempt:
    """One attempt to publish one item, with the registry kept honest.

    Entering the block writes the intent before any external call. Leaving it
    resolves to exactly one of three states:

      confirmed  succeeded() was called
      failed     the block raised KnownFailure — the effect provably did not happen
      pending    the block raised anything else, or exited without confirming —
                 the outcome is unknown and the item stays blocked until
                 reconciliation asks the platform
    """

    def __init__(self, registry, key: str, source_id: str | None = None, **meta):
        self.registry = registry
        self.key = key
        self.source_id = source_id
        self.meta = meta
        self.external_id: str | None = None
        self.confirmed = False

    def __enter__(self) -> "PublishAttempt":
        # claim() decide e prende in carico nella STESSA transazione. Con
        # already_handled() seguito da begin() c'era una finestra in cui un altro
        # processo poteva prendere l'item nel mezzo, e begin() ne avrebbe
        # sovrascritto il record — anche se gia' confermato.
        self.attempt_id = self.registry.claim(self.key, source_id=self.source_id,
                                              **self.meta)
        if self.attempt_id is None:
            raise AlreadySettled(self.key)
        return self

    def record(self, **ids) -> None:
        """Persist an intermediate id the moment the platform returns it.

        This is what makes recovery possible: a pending record holding only the
        intent gives reconciliation nothing to ask the platform about.
        """
        self.registry.progress(self.key, **ids)

    def succeeded(self, external_id: str, **meta) -> None:
        self.external_id = external_id
        self.registry.confirm(self.key, external_id, **meta)
        self.confirmed = True

    def __exit__(self, exc_type, exc, _tb) -> bool:
        if exc_type is None:
            if not self.confirmed:
                # Left the block without confirming and without raising. The
                # publisher has a bug, but the safe reading is still "unknown".
                self.registry.progress(
                    self.key, note="uscito dal blocco senza confermare")
            return False

        if issubclass(exc_type, KnownFailure):
            self.registry.fail(self.key, str(exc))
            return False  # the caller still sees the exception

        # Unknown outcome: leave it pending. Blocked is the safe direction.
        self.registry.progress(self.key, last_error=str(exc)[:300])
        return False


def classify(exc: Exception, known_markers=("invalid", "unsupported", "malformed",
                                            "not allowed", "too large")) -> Exception:
    """Best-effort promotion of an error to KnownFailure.

    Deliberately conservative: an error is only treated as a known non-event when
    its message names a problem with the request itself. Everything ambiguous
    stays unknown, because the cost of the two mistakes is not symmetric — a
    wrongly-blocked item needs a human, a wrongly-retried one publishes twice.
    """
    if isinstance(exc, KnownFailure):
        return exc
    text = str(exc).lower()
    if any(marker in text for marker in known_markers):
        return KnownFailure(str(exc))
    return exc
