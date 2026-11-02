"""Icons were looked up only for the companies on the page in view, two at a time: a page of 25 showed letters for a minute, and the
next page started over (336 of 1,012 companies on the board had an icon, 2026-11-02). The board asks for the best-ranked companies of
the first pages ahead of time, once each."""

from types import SimpleNamespace

from jobpilot import board_view
from jobpilot.logos import Shelf, key_for


def rows(*items):
    return [{"id": f"j{i}", "company": c, "fit": f, "score": s} for i, (c, f, s) in enumerate(items)]


def test_the_best_ranked_companies_come_first_each_once_and_only_good_fits(service):
    board_view.board(service).value = {"rows": rows(("Low Co", "match", 50), ("Top Co", "match", 95), ("Top Co", "match", 90), ("Unranked Co", "match", None), ("Other Co", "other", 99))}
    assert board_view.companies_to_warm(service) == ["Top Co", "Low Co", "Unranked Co"]
    assert board_view.companies_to_warm(service, limit=1) == ["Top Co"]


def test_nothing_before_the_board_is_read(service):
    assert board_view.companies_to_warm(service) == []


async def test_warming_queues_each_company_once_and_never_waits_on_the_network(service, tmp_path, monkeypatch):
    kept = Shelf(tmp_path)
    monkeypatch.setattr("jobpilot.logos.shelf", lambda data_dir: kept)
    fake = SimpleNamespace(config=SimpleNamespace(company_logos=True, data_dir=tmp_path), db=service.db)
    assert board_view.warm_logos(fake, ["Alpha Corp", "Beta Corp", "Alpha Corp"]) == 2
    assert kept.waiting == {key_for("Alpha Corp"), key_for("Beta Corp")}
    assert board_view.warm_logos(fake, ["Alpha Corp"]) == 0  # already waiting


async def test_logos_off_in_settings_queues_nothing(service, tmp_path):
    fake = SimpleNamespace(config=SimpleNamespace(company_logos=False, data_dir=tmp_path), db=service.db)
    assert board_view.warm_logos(fake, ["Alpha Corp"]) == 0
