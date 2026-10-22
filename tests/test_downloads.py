from pathlib import Path

from src.downloads import collect_tasks, plan_downloads, tender_id_from_results
from src.models import LotData, Participant

FOLDER = Path("downloads/1")


def test_same_url_in_several_lots_is_downloaded_once():
    tasks = [("u1", FOLDER / "a" / "doc.pdf"), ("u1", FOLDER / "b" / "doc.pdf")]
    plan = plan_downloads(tasks)
    assert list(plan) == ["u1"]
    assert plan["u1"] == [FOLDER / "a" / "doc.pdf", FOLDER / "b" / "doc.pdf"]


def test_duplicate_task_does_not_duplicate_path():
    path = FOLDER / "doc.pdf"
    assert plan_downloads([("u1", path), ("u1", path)]) == {"u1": [path]}


def test_name_collision_of_different_urls_gets_unique_paths():
    path = FOLDER / "Specificatie tehnica semnata electronic ...pdf"
    plan = plan_downloads([("u1", path), ("u2", path)])
    p1, p2 = plan["u1"][0], plan["u2"][0]
    assert p1 != p2
    assert p1.suffix == p2.suffix == ".pdf"


def test_collision_naming_does_not_depend_on_task_order():
    path = FOLDER / "doc.pdf"
    forward = plan_downloads([("u1", path), ("u2", path)])
    backward = plan_downloads([("u2", path), ("u1", path)])
    assert forward == backward  # иначе докачка между запусками перепутает файлы


def test_collect_tasks_skips_failed_lots_and_filters_extensions():
    ok = LotData(number=1, url="https://achizitii.md/ro/public/tender/123/lot/9", title="Lot nr. 1 Cateter",
                 participants=[Participant(name="SRL A", price_str="", docs=[("a.pdf", "u1"), ("b.zip", "u2")])])
    failed = LotData(number=0, url="x", title="Lot nr. 2", error="404",
                     participants=[Participant(name="SRL B", price_str="", docs=[("c.pdf", "u3")])])
    tasks = collect_tasks([ok, failed], FOLDER)
    assert [url for url, _ in tasks] == ["u1"]
    assert tender_id_from_results([ok]) == "123"
