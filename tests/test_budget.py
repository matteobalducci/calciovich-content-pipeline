"""Tests for the spending ledger.

The README used to promise a spending cap and a per-item retry limit that did
not exist. These assert that both are now real, and — the part that actually
protects the wallet — that they hold when two processes generate at once.
"""

import multiprocessing as mp
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from budget import (  # noqa: E402
    Budget,
    BudgetExceeded,
    TooManyAttempts,
)


@pytest.fixture
def out(tmp_path):
    return str(tmp_path / "output")


def ledger(out, cap=20.0, attempts=3):
    return Budget(out, provider="piapi", monthly_cap_usd=cap,
                  max_attempts_per_item=attempts)


# --- the cap -------------------------------------------------------------


def test_spending_accumulates_across_calls(out):
    b = ledger(out)
    b.reserve(3.0).settle()
    b.reserve(2.0).settle()
    assert b.spent_this_month() == pytest.approx(5.0)
    assert b.remaining() == pytest.approx(15.0)


def test_a_call_that_would_exceed_the_cap_is_refused(out):
    b = ledger(out, cap=10.0)
    b.reserve(8.0).settle()
    with pytest.raises(BudgetExceeded):
        b.reserve(3.0)
    assert b.spent_this_month() == pytest.approx(8.0), "il rifiuto non deve lasciare traccia"


def test_settling_for_less_than_reserved_gives_the_difference_back(out):
    b = ledger(out, cap=10.0)
    r = b.reserve(5.0)
    r.settle(2.0)
    assert b.spent_this_month() == pytest.approx(2.0)
    b.reserve(7.0).settle()          # ora ci sta


def test_release_returns_the_whole_reservation(out):
    b = ledger(out, cap=10.0)
    r = b.reserve(9.0)
    assert b.remaining() == pytest.approx(1.0), "una prenotazione viva occupa budget"
    r.release()
    assert b.remaining() == pytest.approx(10.0)


def test_an_abandoned_reservation_keeps_counting(out):
    """Una prenotazione lasciata da un crash resta contata: sbagliamo per
    difetto, e spendere meno del previsto non fa danni."""
    b = ledger(out, cap=10.0)
    b.reserve(6.0)                   # il processo muore qui

    fresh = ledger(out, cap=10.0)
    assert fresh.spent_this_month() == pytest.approx(6.0)
    with pytest.raises(BudgetExceeded):
        fresh.reserve(5.0)


def test_two_processes_cannot_both_take_the_last_dollars(out):
    """Il controllo e la prenotazione sono una sola transazione: senza, due
    generazioni in parallelo vedrebbero entrambe lo stesso margine."""
    a = ledger(out, cap=10.0)
    b = ledger(out, cap=10.0)
    a.reserve(7.0)
    with pytest.raises(BudgetExceeded):
        b.reserve(7.0)
    assert b.spent_this_month() == pytest.approx(7.0)


# --- the retry limit -----------------------------------------------------


def test_an_item_stops_costing_money_after_too_many_attempts(out):
    """Il caso che bruciava il budget: una scena che continua a fallire e che
    si continua a ritentare."""
    b = ledger(out, cap=100.0, attempts=3)
    for _ in range(3):
        b.reserve(1.0, item="scena-07").settle()
    with pytest.raises(TooManyAttempts):
        b.reserve(1.0, item="scena-07")


def test_a_released_attempt_does_not_count_against_the_limit(out):
    """Se il fornitore non ha fatto nulla, non era un tentativo pagato."""
    b = ledger(out, cap=100.0, attempts=2)
    b.reserve(1.0, item="scena-07").release()
    b.reserve(1.0, item="scena-07").settle()
    b.reserve(1.0, item="scena-07").settle()
    with pytest.raises(TooManyAttempts):
        b.reserve(1.0, item="scena-07")


def test_other_items_are_unaffected_by_one_stubborn_scene(out):
    b = ledger(out, cap=100.0, attempts=2)
    b.reserve(1.0, item="scena-07").settle()
    b.reserve(1.0, item="scena-07").settle()
    with pytest.raises(TooManyAttempts):
        b.reserve(1.0, item="scena-07")
    b.reserve(1.0, item="scena-08").settle()   # deve passare


def test_reset_item_allows_retrying_after_a_fix(out):
    b = ledger(out, cap=100.0, attempts=2)
    b.reserve(1.0, item="scena-07").settle()
    b.reserve(1.0, item="scena-07").settle()
    b.reset_item("scena-07")
    b.reserve(1.0, item="scena-07").settle()   # non deve sollevare


def test_summary_reports_the_real_numbers(out):
    b = ledger(out, cap=20.0)
    b.reserve(4.5).settle()
    text = b.summary()
    assert "4.50" in text and "20.00" in text and "15.50" in text


def _open_budget_at_barrier(barrier, out_dir, q):
    """Corpo del processo figlio: stessa tecnica di
    test_upload_registry.py::test_concurrent_first_open_from_real_separate_processes_does_not_crash
    (Barrier, non subprocess/bash — l'unica sincronizzazione stretta abbastanza
    da far chiamare Budget() a TUTTI i figli nello stesso istante)."""
    from budget import Budget
    barrier.wait()
    try:
        b = Budget(out_dir, provider="piapi")
        b.reserve(0.1, item=f"item-{__import__('os').getpid()}").settle()
        q.put("OK")
    except Exception as exc:
        q.put(f"FAIL {type(exc).__name__}: {exc}")


def test_concurrent_first_open_from_real_separate_processes_does_not_crash(tmp_path):
    """Budget condivide lo stesso pattern non protetto di
    upload_registry.Registry._connect() PRIMA del fix in quel modulo: apre lo
    stesso tipo di file SQLite (stesso DB_NAME), ma con la propria copia
    duplicata (non condivisa) del codice di setup — quindi il fix su Registry
    non copre Budget. L'unico chiamante reale, genera_video_ai.py, apre un
    Budget ad ogni esecuzione: due generazioni a pagamento lanciate quasi
    insieme (es. un retry manuale mentre l'altra e' ancora in corso) aprono lo
    stesso db_path per la prima volta in un istante vicino abbastanza da
    innescare la stessa race sulla transizione a WAL mode. Vedi il test
    gemello in test_upload_registry.py per i dettagli/probabilita' misurate."""
    ctx = mp.get_context("fork")
    n_procs, n_batches = 16, 8
    seen_ok = 0
    failures = []
    for batch in range(n_batches):
        out_dir = str(tmp_path / f"batch_{batch}" / "output" / "ai-clips")
        barrier = ctx.Barrier(n_procs)
        q = ctx.Queue()
        procs = [ctx.Process(target=_open_budget_at_barrier, args=(barrier, out_dir, q))
                 for _ in range(n_procs)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=30)
        results = [q.get(timeout=5) for _ in range(n_procs)]
        seen_ok += sum(1 for r in results if r == "OK")
        failures += [r for r in results if r != "OK"]

    assert seen_ok == n_procs * n_batches, (
        f"{len(failures)}/{n_procs * n_batches} processi sono morti durante "
        f"l'apertura concorrente del ledger: {failures[:3]}"
    )
