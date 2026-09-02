"""End-to-end tests of the publish protocol against a fake platform.

These are the tests the previous audit said were missing: not "does the registry
state machine work in isolation", but "does a publisher that dies at an awkward
moment leave the system in a state where the NEXT run does the right thing".

The fake platform records every call, so a test can assert the thing that
actually matters — whether the item got published twice.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from publish_attempt import KnownFailure, PublishAttempt, classify  # noqa: E402
from upload_registry import CONFIRMED, FAILED, PENDING, Registry, state_of  # noqa: E402


class FakePlatform:
    """Stands in for YouTube / Instagram / TikTok.

    Counts publishes per item, which is the only assertion that really matters:
    a duplicate public post is the failure this whole design exists to prevent.
    """

    def __init__(self):
        self.published = {}      # external_id -> key
        self.publish_calls = []  # every attempt, successful or not
        self.next_failure = None

    def create_container(self, key):
        self.publish_calls.append(("container", key))
        if self.next_failure == "container":
            raise ConnectionError("connessione caduta creando il container")
        return f"container-{key}"

    def publish(self, container, key):
        self.publish_calls.append(("publish", key))
        if self.next_failure == "publish":
            raise ConnectionError("timeout dopo l'invio")
        if self.next_failure == "rejected":
            raise ValueError("invalid video format")
        external_id = f"remote-{key}"
        self.published[external_id] = key
        return external_id

    def find_by_key(self, key):
        for external_id, k in self.published.items():
            if k == key:
                return external_id
        return None

    def times_published(self, key):
        return sum(1 for k in self.published.values() if k == key)


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "youtube-uploads.json")


@pytest.fixture
def platform():
    return FakePlatform()


def run_publisher(registry, platform, key, source_id="s1"):
    """The publish flow, exactly as the real publishers use it."""
    with PublishAttempt(registry, key, source_id=source_id, title=f"titolo {key}") as attempt:
        try:
            container = platform.create_container(key)
            attempt.record(containerId=container)
            external_id = platform.publish(container, key)
        except Exception as exc:
            raise classify(exc) from exc
        attempt.succeeded(external_id, url=f"https://example/{external_id}")
    return attempt.external_id


def reconcile(registry, platform):
    """The recovery a real publisher runs before planning."""
    def probe(key, record):
        if not record.get("containerId"):
            return None  # died before any external effect could happen
        return platform.find_by_key(key)
    return registry.reconcile(probe)


# --- the happy path ------------------------------------------------------


def test_publishes_once_and_confirms(path, platform):
    reg = Registry(path)
    run_publisher(reg, platform, "ep1.mp4")

    assert platform.times_published("ep1.mp4") == 1
    assert state_of(reg.data["ep1.mp4"]) == CONFIRMED
    assert reg.already_handled("ep1.mp4")


def test_a_second_run_does_not_republish(path, platform):
    reg = Registry(path)
    run_publisher(reg, platform, "ep1.mp4")

    fresh = Registry(path)
    if not fresh.already_handled("ep1.mp4", source_id="s1"):
        run_publisher(fresh, platform, "ep1.mp4")

    assert platform.times_published("ep1.mp4") == 1


# --- the crash that started all this -------------------------------------


def test_crash_after_publish_does_not_duplicate_on_the_next_run(path, platform):
    """THE case. The platform accepted the upload, the process died before the
    registry was updated. The next run must recover it, not publish it again."""
    reg = Registry(path)
    platform.next_failure = "publish"

    # The publish call raises *after* the platform accepted it — simulated by
    # letting it register the item and then blowing up on the next attempt.
    with PublishAttempt(reg, "ep1.mp4", source_id="s1", title="t") as attempt:
        container = platform.create_container("ep1.mp4")
        attempt.record(containerId=container)
        platform.published["remote-ep1.mp4"] = "ep1.mp4"   # it landed
        # ...and then the process dies: no succeeded() call.

    assert state_of(reg.data["ep1.mp4"]) == PENDING, "un esito ignoto deve restare pending"
    assert reg.already_handled("ep1.mp4"), "pending deve bloccare un secondo invio"

    # next run
    fresh = Registry(path)
    outcomes = reconcile(fresh, platform)

    assert outcomes == [("ep1.mp4", "recovered as remote-ep1.mp4")]
    assert state_of(fresh.data["ep1.mp4"]) == CONFIRMED
    assert platform.times_published("ep1.mp4") == 1, "NON deve essere ripubblicato"


def test_crash_before_any_external_call_frees_the_item(path, platform):
    """Died before the container existed: nothing can have happened remotely,
    so the item must become eligible again rather than stay blocked."""
    reg = Registry(path)
    reg.begin("ep1.mp4", source_id="s1")   # and then nothing

    fresh = Registry(path)
    reconcile(fresh, platform)

    assert "ep1.mp4" not in fresh.data
    assert not fresh.already_handled("ep1.mp4", source_id="s1")

    run_publisher(fresh, platform, "ep1.mp4")
    assert platform.times_published("ep1.mp4") == 1


def test_network_failure_before_publish_leaves_it_recoverable(path, platform):
    reg = Registry(path)
    platform.next_failure = "container"

    with pytest.raises(ConnectionError):
        run_publisher(reg, platform, "ep1.mp4")

    assert state_of(reg.data["ep1.mp4"]) == PENDING
    assert platform.times_published("ep1.mp4") == 0

    fresh = Registry(path)
    platform.next_failure = None
    reconcile(fresh, platform)          # never landed -> cleared
    run_publisher(fresh, platform, "ep1.mp4")
    assert platform.times_published("ep1.mp4") == 1


# --- known failures must NOT block ---------------------------------------


def test_a_rejected_request_is_marked_failed_and_can_be_retried(path, platform):
    """A validation error provably means nothing was published. Blocking the
    item forever on that would be a bug, not caution."""
    reg = Registry(path)
    platform.next_failure = "rejected"

    with pytest.raises(KnownFailure):
        run_publisher(reg, platform, "ep1.mp4")

    assert state_of(reg.data["ep1.mp4"]) == FAILED
    assert not reg.already_handled("ep1.mp4")

    platform.next_failure = None
    run_publisher(reg, platform, "ep1.mp4")
    assert platform.times_published("ep1.mp4") == 1
    assert state_of(reg.data["ep1.mp4"]) == CONFIRMED


def test_classify_is_conservative_about_unknown_errors(path):
    """The two mistakes do not cost the same: a wrongly blocked item needs a
    human, a wrongly retried one publishes twice. So anything ambiguous stays
    unknown."""
    assert isinstance(classify(ValueError("invalid video format")), KnownFailure)
    assert not isinstance(classify(TimeoutError("read timed out")), KnownFailure)
    assert not isinstance(classify(ConnectionError("connection reset")), KnownFailure)
    assert not isinstance(classify(RuntimeError("500 internal server error")), KnownFailure)


# --- reconciliation refuses to guess -------------------------------------


def test_unreachable_platform_keeps_the_item_blocked(path, platform):
    reg = Registry(path)
    reg.begin("ep1.mp4", source_id="s1")
    reg.progress("ep1.mp4", containerId="c-1")

    def broken_probe(key, record):
        raise ConnectionError("API irraggiungibile")

    fresh = Registry(path)
    outcomes = fresh.reconcile(broken_probe)

    assert outcomes == [("ep1.mp4", "unresolved (ConnectionError)")]
    assert fresh.already_handled("ep1.mp4"), "senza risposta si resta bloccati"


def test_two_items_recover_independently(path, platform):
    reg = Registry(path)
    reg.begin("a.mp4", source_id="a"); reg.progress("a.mp4", containerId="c-a")
    reg.begin("b.mp4", source_id="b"); reg.progress("b.mp4", containerId="c-b")
    platform.published["remote-a.mp4"] = "a.mp4"   # solo 'a' e' arrivato

    fresh = Registry(path)
    reconcile(fresh, platform)

    assert state_of(fresh.data["a.mp4"]) == CONFIRMED
    assert "b.mp4" not in fresh.data, "quello mai arrivato torna disponibile"
