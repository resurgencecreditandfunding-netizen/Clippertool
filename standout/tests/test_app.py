import datetime as dt
import itertools

import pytest
from fastapi.testclient import TestClient

from standout import automation, content
from standout.db import db
from standout.main import app

_n = itertools.count()
PROFILE = {"business": "Rapid Rooter", "owner": "Mike", "trade": "plumbing", "city": "Tampa",
           "phone": "(813) 555-0100", "services": "water heaters, drain cleaning, leak repair",
           "offer": "$75 off water heater installs", "problems": "slow drains, no hot water"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        email = f"owner{next(_n)}@example.com"
        r = c.post("/api/signup", json={"email": email, "password": "password123", "business": "Rapid Rooter"})
        assert r.status_code == 200, r.text
        assert c.put("/api/profile", json=PROFILE).json()["missing"] == []
        yield c


def test_requires_login():
    with TestClient(app) as c:
        assert c.get("/api/me").status_code == 401
        assert c.get("/", follow_redirects=False).status_code == 303


def test_login_and_bad_password(client):
    email = client.get("/api/me").json()["email"]
    with TestClient(app) as c:
        assert c.post("/api/login", json={"email": email, "password": "nope-nope"}).status_code == 400
        assert c.post("/api/login", json={"email": email, "password": "password123"}).status_code == 200


def test_four_funnels_filled_from_profile(client):
    fs = client.get("/api/funnels").json()
    assert [f["key"] for f in fs] == ["estimate", "special", "emergency", "referral"]
    est = fs[0]
    assert "Tampa" in est["ad"]["primary"] and est["ad"]["link"].endswith("/estimate")
    page = client.get(est["link"].replace("http://localhost:8000", ""))
    assert page.status_code == 200
    assert "Free Plumbing Estimate in Tampa" in page.text and "Rapid Rooter" in page.text


def test_funnel_lead_lands_in_crm_and_gets_followed_up(client):
    slug = client.get("/api/me").json()["slug"]
    r = client.post(f"/f/{slug}/estimate", data={"name": "Jane Doe", "phone": "813-555-0199",
                                                 "email": "jane@example.com", "service": "water heater"})
    assert r.status_code == 200 and "Jane" in r.text
    automation.run_due()
    leads = client.get("/api/contacts").json()
    jane = next(c for c in leads if c["name"] == "Jane Doe")
    assert jane["source"] == "estimate" and jane["stage"] == "new" and jane["seq_status"] == "active"
    detail = client.get(f"/api/contacts/{jane['id']}").json()
    channels = [m["channel"] for m in detail["messages"]]
    assert channels == ["sms", "email"]  # the two "right away" steps
    assert "Hi Jane, this is Mike with Rapid Rooter" in detail["messages"][0]["body"]
    assert detail["messages"][0]["status"] == "simulated"

    # Time passes: next step goes out
    with db() as conn:
        conn.execute("UPDATE contacts SET created = created - 3*3600, seq_next = seq_next - 3*3600 WHERE id = ?", (jane["id"],))
    automation.run_due()
    assert len(client.get(f"/api/contacts/{jane['id']}").json()["messages"]) == 3

    # Owner moves the lead: follow-up stops
    client.patch(f"/api/contacts/{jane['id']}", json={"stage": "contacted"})
    with db() as conn:
        conn.execute("UPDATE contacts SET created = created - 30*86400 WHERE id = ?", (jane["id"],))
    automation.run_due()
    j = client.get(f"/api/contacts/{jane['id']}").json()
    assert len(j["messages"]) == 3 and j["seq_status"].startswith("stopped")


def test_reply_stops_sequence(client):
    slug = client.get("/api/me").json()["slug"]
    client.post(f"/f/{slug}/special", data={"name": "Bob", "phone": "+1 (727) 555-0142"})
    automation.handle_inbound_sms("+17275550142", "Yes please")
    bob = next(c for c in client.get("/api/contacts").json() if c["name"] == "Bob")
    assert bob["seq_status"] == "stopped: replied"
    assert client.get(f"/api/contacts/{bob['id']}").json()["messages"][-1]["direction"] == "in"


def test_honeypot_and_validation(client):
    slug = client.get("/api/me").json()["slug"]
    client.post(f"/f/{slug}/estimate", data={"name": "Bot", "phone": "1", "website": "spam.com"})
    assert not any(c["name"] == "Bot" for c in client.get("/api/contacts").json())
    assert client.post(f"/f/{slug}/estimate", data={"name": "", "phone": ""}).status_code == 400


def test_disabled_funnel_is_hidden(client):
    slug = client.get("/api/me").json()["slug"]
    client.put("/api/funnels/emergency", json={"enabled": False})
    assert client.get(f"/f/{slug}/emergency").status_code == 404


def test_contact_limit(client):
    for i in range(5):
        assert client.post("/api/contacts", json={"name": f"P{i}"}).status_code == 200
    r = client.post("/api/contacts", json={"name": "One too many"})
    assert r.status_code == 400 and "limit" in r.json()["detail"]
    # Public form over the limit: lead is forwarded to the owner, not dropped with an error
    slug = client.get("/api/me").json()["slug"]
    assert client.post(f"/f/{slug}/estimate", data={"name": "Late", "phone": "555"}).status_code == 200


def test_calendar_follows_weekly_rhythm(client):
    start = content.next_monday(dt.date(2026, 11, 20))
    assert start == dt.date(2026, 11, 23)
    r = client.post("/api/content/generate", json={"start": start.isoformat(), "days": 30}).json()
    assert r["created"] == 30
    posts = client.get(f"/api/posts?start={start}&end=2027-01-31").json()["posts"]
    assert len(posts) == 30
    assert [p["pillar"] for p in posts[:7]] == ["Teach", "Behind the Scenes", "Proof", "Mistake / Myth",
                                                "Offer", "Local", "Story"]
    friday = posts[4]
    assert friday["funnel"] == "special" and "$75 off water heater installs" in friday["script"]
    assert "HOOK" in friday["script"] and "FILM IT" in friday["script"]
    assert f"/f/" in friday["caption"]
    # no unfilled placeholders anywhere
    assert not any("{" in p["script"] + p["caption"] + p["title"] for p in posts)
    # schedule + edit
    pid = posts[0]["id"]
    assert client.patch(f"/api/posts/{pid}", json={"status": "scheduled", "time": "08:15"}).status_code == 200
    assert client.patch(f"/api/posts/{pid}", json={"status": "bogus"}).status_code == 400


def test_scheduled_post_due_sends_reminder(client):
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    client.post("/api/content/generate", json={"start": yesterday, "days": 7})
    p = client.get(f"/api/posts?start={yesterday}&end={yesterday}").json()["posts"][0]
    client.patch(f"/api/posts/{p['id']}", json={"status": "scheduled"})
    automation.run_due_posts()
    p2 = client.get(f"/api/posts?start={yesterday}&end={yesterday}").json()["posts"][0]
    assert p2["status"] == "reminded"


def test_sequence_edit_validation(client):
    bad = client.put("/api/funnels/estimate", json={"sequence": [{"delay": 0, "channel": "fax", "body": "hi"}]})
    assert bad.status_code == 400
    ok = client.put("/api/funnels/estimate", json={"sequence": [{"delay": 60, "channel": "sms", "body": "Hi {first}"}]})
    assert ok.status_code == 200
    assert client.get("/api/funnels").json()[0]["sequence"][0]["delay"] == 60
    client.post("/api/funnels/estimate/reset")
    assert len(client.get("/api/funnels").json()[0]["sequence"]) == 6


def test_accounts_are_isolated(client):
    client.post("/api/contacts", json={"name": "Private Lead"})
    with TestClient(app) as other:
        other.post("/api/signup", json={"email": f"x{next(_n)}@example.com", "password": "password123", "business": "Other Co"})
        assert other.get("/api/contacts").json() == []
        cid = client.get("/api/contacts").json()[0]["id"]
        assert other.get(f"/api/contacts/{cid}").status_code == 404


def test_csv_round_trip(client):
    r = client.post("/api/contacts/import", content="Name,Phone,Email\nAl,555-1,al@x.com\n,,\n",
                    headers={"Content-Type": "text/plain"})
    assert r.json() == {"added": 1, "skipped": 1}
    assert "Al,555-1,al@x.com" in client.get("/api/contacts.csv").text
