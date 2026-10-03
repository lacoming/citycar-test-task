"""Тесты на имитации amoCRM API v4: сеть не нужна, токен не нужен."""
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import amo_problem_deals as amo  # noqa: E402

BASE = "https://example.amocrm.ru/api/v4"
NOW = 1_760_000_000  # фиксированное «сейчас», чтобы тесты не зависели от часов


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Отдаёт заранее заданные страницы по пути и номеру страницы, запоминает запросы."""

    def __init__(self, pages):
        self.pages = pages  # {"tasks": [[...], [...]], "leads": [[...]]}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        assert timeout, "каждый запрос должен идти с таймаутом"
        key = url.rsplit("/", 1)[-1]
        self.calls.append((key, dict(params or {})))
        pages = self.pages.get(key, [])
        page = int(params.get("page", 1))
        if not pages:
            return FakeResponse(204)
        if page > len(pages):
            return FakeResponse(204)
        links = {"self": {"href": url}}
        if page < len(pages):
            links["next"] = {"href": f"{url}?page={page + 1}"}
        return FakeResponse(200, {"_embedded": {key: pages[page - 1]}, "_links": links})


def task(lead_id, complete_till):
    return {"id": lead_id * 100 + complete_till % 7, "entity_id": lead_id, "entity_type": "leads",
            "complete_till": complete_till, "is_completed": False}


def lead(lead_id, status_id=1001):
    return {"id": lead_id, "name": f"Сделка {lead_id}", "status_id": status_id, "responsible_user_id": 7}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(amo.time, "sleep", lambda _: None)


def run(pages):
    session = FakeSession(pages)
    result = [(l["id"], reason) for l, reason in amo.problem_deals(session, BASE, NOW)]
    return result, session


def test_classifies_deals():
    result, _ = run({
        "tasks": [[task(1, NOW - 3600), task(2, NOW + 3600), task(4, NOW - 60), task(4, NOW - 120)]],
        "leads": [[lead(1), lead(2), lead(3), lead(4)]],
    })
    assert result == [(1, "просрочено задач: 1"), (3, "нет открытых задач"), (4, "просрочено задач: 2")]


def test_deal_with_future_and_overdue_task_is_reported():
    result, _ = run({"tasks": [[task(1, NOW + 86400), task(1, NOW - 86400)]], "leads": [[lead(1)]]})
    assert result == [(1, "просрочено задач: 1")]


def test_closed_deals_are_skipped():
    result, _ = run({"tasks": [], "leads": [[lead(1, status_id=142), lead(2, status_id=143), lead(3)]]})
    assert result == [(3, "нет открытых задач")]


def test_walks_all_pages_and_keeps_filters():
    result, session = run({
        "tasks": [[task(1, NOW - 10)], [task(2, NOW - 10)]],
        "leads": [[lead(1)], [lead(2)], [lead(3)]],
    })
    assert result == [(1, "просрочено задач: 1"), (2, "просрочено задач: 1"), (3, "нет открытых задач")]
    task_calls = [p for key, p in session.calls if key == "tasks"]
    assert [p["page"] for p in task_calls] == [1, 2]
    for p in task_calls:  # фильтр не теряется на второй странице
        assert p["filter[entity_type]"] == "leads"
        assert p["filter[is_completed]"] == 0
        assert p["limit"] == 250


def test_empty_account_returns_nothing():
    result, session = run({})
    assert result == []
    assert [key for key, _ in session.calls] == ["tasks", "leads"]


def test_http_error_is_raised():
    class Broken(FakeSession):
        def get(self, url, params=None, timeout=None):
            return FakeResponse(401)

    with pytest.raises(RuntimeError, match="401"):
        list(amo.problem_deals(Broken({}), BASE, NOW))


def test_import_does_not_need_env(monkeypatch):
    monkeypatch.delenv("AMO_SUBDOMAIN", raising=False)
    monkeypatch.delenv("AMO_TOKEN", raising=False)
    importlib.reload(amo)
