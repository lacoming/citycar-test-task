"""Сделки amoCRM без открытых задач или с просроченными задачами."""
import os
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

CLOSED = {142, 143}  # системные статусы «успешно реализовано» и «закрыто», общие для всех воронок


def fetch_all(session, url: str, key: str, params=None):
    """Обходит все страницы списка amoCRM; ответ 204 означает, что данных нет."""
    page = 1
    while True:
        r = session.get(url, params={**(params or {}), "page": page, "limit": 250}, timeout=30)
        if r.status_code == 204:
            return
        r.raise_for_status()
        data = r.json()
        yield from data["_embedded"][key]
        if "next" not in data.get("_links", {}):
            return
        page += 1
        time.sleep(0.15)  # лимит amoCRM - 7 запросов в секунду


def problem_deals(session, base: str, now: float):
    """Отдаёт (сделка, причина) для открытых сделок без задач или с просроченными задачами."""
    deadlines: dict[int, list[int]] = {}  # храним только сроки, а не задачи целиком
    flt = {"filter[entity_type]": "leads", "filter[is_completed]": 0}
    for t in fetch_all(session, f"{base}/tasks", "tasks", flt):
        deadlines.setdefault(t["entity_id"], []).append(t["complete_till"])
    for lead in fetch_all(session, f"{base}/leads", "leads"):
        if lead["status_id"] in CLOSED:
            continue
        late = sum(d < now for d in deadlines.get(lead["id"], []))
        if lead["id"] not in deadlines:
            yield lead, "нет открытых задач"
        elif late:
            yield lead, f"просрочено задач: {late}"


if __name__ == "__main__":
    sub = os.environ["AMO_SUBDOMAIN"]
    api = requests.Session()
    api.headers["Authorization"] = f"Bearer {os.environ['AMO_TOKEN']}"  # долгосрочный токен интеграции
    retry = Retry(total=5, backoff_factor=1, status_forcelist=(429, 502, 503, 504))  # 401 не повторяем
    api.mount("https://", HTTPAdapter(max_retries=retry))
    for lead, reason in problem_deals(api, f"https://{sub}.amocrm.ru/api/v4", time.time()):
        print(lead["id"], lead["name"], reason, f"https://{sub}.amocrm.ru/leads/detail/{lead['id']}", sep="\t")
