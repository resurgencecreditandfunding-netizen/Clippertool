"""Competitor review intelligence + subscription status."""
import itertools

import pytest
from fastapi.testclient import TestClient

from standout import auth, automation, reviews
from standout.main import app

_n = itertools.count(5000)

BAD = [
    {"rating": 1, "snippet": "Waited all day and they never showed. Nobody called back either."},
    {"rating": 1, "snippet": "Quoted $300, charged me $780. Hidden fee after hidden fee. Total rip off."},
    {"rating": 2, "snippet": "Tech was rude and kept trying to upsell me a whole new system."},
    {"rating": 1, "snippet": "Left mud all over my carpet and the AC still not fixed two days later."},
    {"rating": 5, "snippet": "Great service!"},
]


def owner(c, **extra):
    c.post("/api/signup", json={"email": f"e{next(_n)}@example.com", "password": "password123", "business": "Cool Breeze HVAC"})
    c.put("/api/profile", json={"trade": "HVAC", "city": "Tampa", "phone": "8135550100", "services": "AC repair",
                                "offer": "$49 tune-up", **extra})
    return c.get("/api/me").json()


@pytest.fixture
def serp(monkeypatch):
    calls = []

    def fake(params):
        calls.append(params)
        if params["engine"] == "google_maps":
            return {"local_results": [
                {"title": "Cool Breeze HVAC", "data_id": "own", "reviews": 900},  # the owner: skipped
                {"title": "Arctic Kings", "data_id": "c1", "rating": 3.9, "reviews": 410},
                {"title": "Tampa Chill Co", "data_id": "c2", "rating": 4.1, "reviews": 220}]}
        return {"reviews": BAD}

    monkeypatch.setattr(reviews, "_serp", fake)
    monkeypatch.setattr(reviews.settings, "serpapi_key", "test")
    return calls


def test_keyword_themes_rank_complaints():
    themes = reviews.keyword_themes(reviews.parse_pasted("\n\n".join(r["snippet"] for r in BAD[:4])))
    keys = [t["key"] for t in themes]
    assert {"no_show", "communication", "price", "pushy", "mess", "quality"} <= set(keys)
    assert all(t["promise"].startswith("We") or t["promise"].startswith("Every") or t["promise"].startswith("You")
               or t["promise"].startswith("Done") or t["promise"].startswith("No") or t["promise"].startswith("Our")
               for t in themes)


def test_google_lookup_skips_own_business_and_keeps_only_low_stars(serp):
    with TestClient(app) as c:
        owner(c)
        r = c.post("/api/edge/run", json={}).json()
        names = [x["name"] for x in r["report"]["competitors"]]
        assert names == ["Arctic Kings", "Tampa Chill Co"]
        assert r["report"]["reviews_analyzed"] == 8  # 4 bad reviews x 2 competitors; the 5-star one dropped
        assert all(p.get("sort_by") == "ratingLow" for p in serp if p["engine"] == "google_maps_reviews")
        assert all(d["on"] is False for d in r["distinguishers"])  # nothing is promised until the owner confirms
        # paid lookups are rate-limited
        assert c.post("/api/edge/run", json={}).status_code == 429


def test_confirmed_promises_flow_into_funnels_ads_and_scripts_without_competitor_names(serp):
    with TestClient(app) as c:
        me = owner(c)
        ds = c.post("/api/edge/run", json={}).json()["distinguishers"]
        ds[0]["on"] = True
        ds[0]["promise"] = "We text you 30 minutes before we arrive."
        assert c.put("/api/edge", json={"distinguishers": ds}).json()["confirmed"] == 1
        page = c.get(f"/f/{me['slug']}/estimate?preview=1").text
        assert "We text you 30 minutes before we arrive." in page
        f = c.get("/api/funnels").json()[0]
        assert f["gap_ads"] and "30 minutes" in f["gap_ads"][0]["primary"]
        c.post("/api/content/generate", json={"start": "2027-01-04", "days": 7})
        posts = c.get("/api/posts?start=2027-01-04&end=2027-01-10").json()["posts"]
        thursday = next(p for p in posts if p["pillar"] == "Mistake / Myth")
        assert "30 minutes" in thursday["script"]
        public = page + str(f["gap_ads"]) + str([p["script"] + p["caption"] for p in posts])
        assert "Arctic" not in public and "Tampa Chill" not in public
        # the owner still sees the private report
        assert c.get("/api/edge").json()["report"]["competitors"][0]["name"] == "Arctic Kings"
        assert "edge_report" not in c.get("/api/me").json()["profile"]


def test_pasted_reviews_work_without_api():
    with TestClient(app) as c:
        owner(c)
        assert c.post("/api/edge/run", json={}).status_code == 400  # no SerpApi key configured
        r = c.post("/api/edge/run", json={"pasted": "\n\n".join(x["snippet"] for x in BAD)})
        assert r.status_code == 200 and r.json()["report"]["source"] == "pasted"


def test_ai_names_are_filtered(monkeypatch):
    monkeypatch.setattr(reviews.settings, "anthropic_api_key", "")
    themes, used = reviews.analyze(reviews.parse_pasted(BAD[0]["snippet"]), {}, ["Arctic Kings"])
    assert not used and themes


def test_subscription_status_controls_access():
    admin_email = f"adm{next(_n)}@example.com"
    auth.create_account(admin_email, "Admin", "adminpass123", admin=True)
    with TestClient(app) as cust, TestClient(app) as adm:
        me = owner(cust)
        adm.post("/api/login", json={"email": admin_email, "password": "adminpass123"})
        cid = next(x["id"] for x in adm.get("/api/admin/customers").json() if x["email"] == me["email"])
        slug = me["slug"]
        cust.post(f"/f/{slug}/estimate", data={"name": "Pat", "phone": "8135550111"})

        adm.patch(f"/api/admin/customers/{cid}", json={"plan_status": "past_due", "setup_paid": True})
        assert cust.get("/api/me").json()["plan_status"] == "past_due"
        assert cust.get("/api/contacts").status_code == 200  # grace period: still works

        adm.patch(f"/api/admin/customers/{cid}", json={"plan_status": "canceled"})
        assert cust.get("/api/contacts").status_code == 402
        assert cust.get(f"/f/{slug}/estimate").status_code == 404
        from standout.db import db
        with db() as conn:  # make every remaining follow-up step due right now
            conn.execute("UPDATE contacts SET seq_next = 0, created = created - 30*86400 WHERE account_id = ?", (cid,))
            before = conn.execute("SELECT COUNT(*) FROM messages WHERE account_id = ?", (cid,)).fetchone()[0]
        automation.run_due()
        with db() as conn:
            after = conn.execute("SELECT COUNT(*) FROM messages WHERE account_id = ?", (cid,)).fetchone()[0]
        assert after == before  # canceled: no texts or emails go out
        row = next(x for x in adm.get("/api/admin/customers").json() if x["id"] == cid)
        assert row["plan_status"] == "canceled" and row["setup_paid"] is True
        assert adm.patch(f"/api/admin/customers/{cid}", json={"plan_status": "free"}).status_code == 400

        adm.patch(f"/api/admin/customers/{cid}", json={"plan_status": "active"})
        assert cust.get("/api/contacts").status_code == 200
