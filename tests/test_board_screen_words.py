"""What the Job board, its job panel and Reserved say about a job, read from their source: words a person takes as the truth about what
AXIOM will do (owner, 2026-11-01: the page "feels sketchy and untrustworthy", and it is unclear where the queue is). Each was seen wrong in
a real browser on an empty install. The browser pass itself is in the v3 report; these keep the words from coming back."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "frontend" / "src"


def read(name):
    return (SRC / name).read_text()


def test_a_job_with_no_place_in_line_is_not_called_in_queue_on_its_panel():
    # job:ready's own label stays "In queue" for the lists; the panel said it above "It won't start until you queue it".
    panel = read("jobdetail.tsx")
    assert '"In queue" : state === "job:ready" ? "Not queued"' in panel
    assert "<StatusPill state={state} label={word} />" in panel


def test_a_held_reserved_job_is_not_called_in_queue():
    held = read("reserved.tsx")
    assert '"job:" + r.job.status' not in held
    assert "Needs your approval" in held


def test_approving_a_reserved_job_does_not_also_open_its_panel():
    held = read("reserved.tsx")
    button = held.index("Approve and apply")
    assert "e.stopPropagation()" in held[button - 420 : button]


def test_a_row_says_not_queued_and_its_number_opens_the_queue():
    board = read("jobboard.tsx")
    assert 'r.state === "ready") return <span className="bd-state is-idle">Not queued</span>' in board
    assert 'onQueue={() => go({ view: "queue" })}' in board
    assert re.search(r'plural\(inTheQueue, "job"\)\} in the queue', board)
    assert "Open the queue" in board


def test_an_empty_board_says_why_and_what_to_do():
    board = read("jobboard.tsx")
    for words in ("No search has run yet", "Tell AXIOM which roles you want", "No good matches right now", "Show everything found"):
        assert words in board, words


def test_a_queue_that_will_not_move_is_said_on_the_board():
    board = read("jobboard.tsx")
    assert "AXIOM is stopped, so jobs in the queue wait" in board
    assert "Sending is off, so jobs in the queue wait" in board
    assert "canApply" not in board  # worked out and never shown


def test_how_to_apply_keeps_its_width_in_a_row():
    # The pinned column squeezed it to an empty box on every row of the board.
    css = read("styles/queue.css")
    rule = css[css.index(".ax-act .tl-choice.is-compact .tl-choice-pick") :]
    assert "flex: none" in rule[: rule.index("}")] and "width: 88px" in rule[: rule.index("}")]


def test_words_with_no_link_in_them_are_answered():
    assert "That isn't a web link." in read("jobintake.tsx")


def test_each_confirmation_names_what_its_button_does():
    assert "confirmLabel={asking" in read("jobboard.tsx")
    assert "{confirmLabel ?? `Queue ${noun}`}" in read("bulk.tsx")
