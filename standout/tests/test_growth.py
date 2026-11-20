"""Admin, brand import, custom domains, brand voice, Pixel + Conversions API."""
import hashlib
import itertools
import json
import re

import pytest
from fastapi.testclient import TestClient

from standout import auth, brand, meta, voice
from standout.main import app

_n = itertools.count(1000)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def new_owner(c, **profile):
    c.post("/api/signup", json={"email": f"o{next(_n)}@example.com", "password": "password123", "business": "Acme Air"})
    c.put("/api/profile", json={"trade": "HVAC", "city": "Tampa", "phone": "813-555-0100", "owner": "Sam",
                                "services": "AC repair", "offer": "$49 tune-up", **profile})
    return c.get("/api/me").json()


@pytest.fixture
def captured(monkeypatch):
    """Capture Conversions API payloads instead of calling Meta."""
    sent = []
    monkeypatch.setattr(meta, "send", lambda profile, events: (sent.extend(events), (True, "received 1"))[1])
    monkeypatch.setattr(meta, "send_later", lambda aid, prof, events: meta.send_and_log(aid, prof, events)
                        if meta.connected(prof) else None)
    return sent


# ------------------------------------------------------------------ admin + enrollment
def test_admin_enrolls_customer_with_setup_link():
    admin_email = f"admin{next(_n)}@example.com"
    auth.create_account(admin_email, "Admin", "adminpass123", admin=True)
    with TestClient(app) as adm:
        r = adm.post("/api/login", json={"email": admin_email, "password": "adminpass123"})
        assert r.json()["admin"] is True
        made = adm.post("/api/admin/customers", json={"email": f"cust{next(_n)}@example.com", "business": "Cool Roofs",
                                                       "contact_limit": 250}).json()
        token = made["setup_link"].split("t=")[1]
        listed = next(c for c in adm.get("/api/admin/customers").json() if c["id"] == made["id"])
        assert listed["contact_limit"] == 250 and listed["pct"] < 100 and not listed["onboarded"]

        with TestClient(app) as cust:
            assert cust.post("/api/setup-password", json={"token": token, "password": "newpass123"}).status_code == 200
            me = cust.get("/api/me").json()
            assert me["profile"]["business"] == "Cool Roofs" and me["onboarded"] is False and me["contact_limit"] == 250
            assert cust.get("/api/admin/customers").status_code == 403  # customers can't see admin
            # setup link works only once
            assert cust.post("/api/setup-password", json={"token": token, "password": "another123"}).status_code == 400

        # act as the customer, change something, come back
        assert adm.post(f"/api/admin/customers/{made['id']}/act-as").status_code == 200
        me = adm.get("/api/me").json()
        assert me["acting_as"] and me["profile"]["business"] == "Cool Roofs"
        adm.put("/api/profile", json={"offer": "Free roof inspection"})
        assert adm.post("/api/admin/return").status_code == 200
        assert adm.get("/api/admin/customers").status_code == 200
        assert adm.get(f"/api/admin/customers/{made['id']}").json()["profile"]["offer"] == "Free roof inspection"


def test_signup_cannot_become_admin():
    with TestClient(app) as c:
        new_owner(c)
        assert c.get("/api/admin/customers").status_code == 403
        assert c.post("/api/admin/customers", json={"email": "x@y.com", "business": "X"}).status_code == 403


# ------------------------------------------------------------------ brand import
def test_palette_skips_greys_and_dedupes_hues():
    css = "a{color:#ff5a1f} b{color:#ff5a1f} .x{background:#ffffff;color:#333} .y{color:#0b3d91} .z{color:#ff6a2f}"
    assert brand.palette(css) == ["#ff5a1f", "#0b3d91"]
    assert brand.palette("", "#123abc")[0] == "#123abc"
    assert brand.main_font("body{font-family:'Montserrat',sans-serif} h1{font-family:Montserrat}") == "Montserrat"


def test_import_site_extracts_brand(monkeypatch):
    html = b"""<html><head><title>Acme Air | Tampa HVAC</title><meta name="theme-color" content="#d62828">
      <meta name="description" content="Family-owned AC repair since 1998."><link rel="stylesheet" href="/s.css"></head>
      <body><img class="site-logo" src="/logo.png"><h1>Cool homes, honest prices</h1>
      <p>We treat every home like our own. No upsells, ever.</p><script>var x='ignore me';</script></body></html>"""
    pages = {"https://acme.test/": (html, "text/html"), "https://acme.test/s.css": (b"a{color:#003049}", "text/css"),
             "https://acme.test/logo.png": (PNG, "image/png")}
    monkeypatch.setattr(brand, "fetch", lambda url, limit, kinds=(): (*pages[url], url))
    site = brand.import_site("acme.test/")
    assert site["colors"][:2] == ["#d62828", "#003049"]
    assert site["logo_url"] == "https://acme.test/logo.png" and site["name"] == "Acme Air"
    assert "honest prices" in site["text"] and "ignore me" not in site["text"]

    with TestClient(app) as c:
        new_owner(c)
        r = c.post("/api/brand/import", json={"url": "acme.test/"}).json()
        assert r["logo"].startswith("/media/") and r["colors"][0] == "#d62828"
        me = c.get("/api/me").json()
        assert me["profile"]["color"] == "#d62828" and me["profile"]["has_site_text"]
        page = c.get(f"/f/{me['slug']}/estimate?preview=1").text
        assert "--brand:#d62828" in page and r["logo"] in page


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost:8000/", "http://169.254.169.254/latest",
                                 "http://10.0.0.5/", "file:///etc/passwd", "ftp://example.com"])
def test_fetch_refuses_private_and_odd_addresses(url):
    with pytest.raises(brand.FetchError):
        brand.fetch(brand.normalize_url(url) if "://" not in url else url, 1000)


def test_logo_upload_and_safe_serving():
    with TestClient(app) as c:
        new_owner(c)
        r = c.post("/api/brand/logo", files={"file": ("l.png", PNG, "image/png")})
        assert r.status_code == 200
        img = c.get(r.json()["logo"])
        assert img.status_code == 200 and img.headers["content-type"] == "image/png"
        svg = c.post("/api/brand/logo", files={"file": ("l.svg", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "image/svg+xml")})
        assert "sandbox" in c.get(svg.json()["logo"]).headers["content-security-policy"]
        bad = c.post("/api/brand/logo", files={"file": ("x.png", b"<html>not an image</html>", "image/png")})
        assert bad.status_code == 400
        assert c.get("/media/1/../../secret.key").status_code == 404


# ------------------------------------------------------------------ custom domains
def test_custom_domain_serves_funnels():
    with TestClient(app) as c:
        me = new_owner(c)
        assert c.put("/api/domain", json={"domain": "https://Go.AcmeAir.com/"}).json()["domain"] == "go.acmeair.com"
        assert c.put("/api/domain", json={"domain": "not a domain"}).status_code == 400
        assert "go.acmeair.com/estimate" in c.get("/api/funnels").json()[0]["link"]
        home = c.get("/", headers={"host": "go.acmeair.com"})
        assert home.status_code == 200 and "Acme Air" in home.text and 'action="/estimate"' in home.text
        assert c.get("/special", headers={"host": "go.acmeair.com"}).status_code == 200
        assert c.get("/api/me", headers={"host": "go.acmeair.com"}).status_code == 404  # no app on their domain
        lead = c.post("/estimate", headers={"host": "go.acmeair.com"}, data={"name": "Dom Lead", "phone": "8135550000"})
        assert lead.status_code == 200
        assert any(x["name"] == "Dom Lead" for x in c.get("/api/contacts").json())
        assert c.get("/internal/tls-check?domain=go.acmeair.com").status_code == 200
        assert c.get("/internal/tls-check?domain=evil.com").status_code == 404
    with TestClient(app) as other:
        new_owner(other)
        assert other.put("/api/domain", json={"domain": "go.acmeair.com"}).status_code == 400


# ------------------------------------------------------------------ brand voice
def test_voice_fallback_applies_tone_and_signoff():
    with TestClient(app) as c:
        new_owner(c, tone="friendly", signoff="Talk soon,")
        r = c.post("/api/voice/generate").json()
        assert r["used_ai"] is False and r["steps"] == 15
        est = c.get("/api/funnels").json()[0]["sequence"]
        assert est[0]["body"].startswith("Hey {first}")
        assert "Talk soon,\n{owner}" in est[1]["body"]


def test_voice_ai_result_is_validated(monkeypatch):
    class FakeStream:
        def __init__(self, payload): self.payload = payload
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get_final_message(self):
            class B: type = "text"; text = json.dumps(self.payload)
            class M: stop_reason = "end_turn"; content = [B()]
            return M()

    from standout import funnels
    seqs = {k: [{"subject": "Yo {first}", "body": "Hey {first}! {business} here. {bogus} Call {phone}."}
                for _ in funnels.FUNNELS[k]["sequence"]] for k in funnels.FUNNELS}
    seqs["referral"] = seqs["referral"][:1]  # wrong shape -> that funnel keeps its safe version
    payload = {"voice_guide": "- Casual and warm", "sequences": seqs}

    import anthropic
    class FakeClient:
        def __init__(self, **kw):
            self.beta = type("b", (), {"messages": type("m", (), {"stream": staticmethod(lambda **kw: FakeStream(payload))})()})()
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    monkeypatch.setattr(voice.settings, "anthropic_api_key", "test")
    out, guide, used = voice.rewrite({"tone": "friendly"}, {})
    assert used and guide == "- Casual and warm"
    assert out["estimate"][0]["body"] == "Hey {first}! {business} here. bogus Call {phone}."
    assert out["estimate"][0]["subject"] == ""          # texts never get subjects
    assert out["estimate"][0]["delay"] == 0 and out["estimate"][2]["delay"] == 7200  # timing untouched
    assert out["referral"][0]["body"].startswith("Thanks so much")  # fallback kept


# ------------------------------------------------------------------ Pixel + Conversions API
def test_matching_is_normalized_and_hashed():
    m = meta.matching(" Jane@Example.COM ", "(813) 555-0199", "Jane Q. Doe", "33602-1234", "7:9")
    sha = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731
    assert m["em"] == [sha("jane@example.com")] and m["ph"] == [sha("18135550199")]
    assert m["fn"] == [sha("jane")] and m["ln"] == [sha("doe")] and m["zp"] == [sha("33602")]
    assert m["country"] == [sha("us")] and m["external_id"] == [sha("7:9")]
    assert meta.make_fbc("", "fb.1.1.abc") == "fb.1.1.abc"
    assert re.fullmatch(r"fb\.1\.\d{13}\.IwAR1234567890", meta.make_fbc("IwAR1234567890"))


def test_funnel_events_are_deduplicated_and_crm_stages_reported(captured):
    with TestClient(app) as c:
        me = new_owner(c, pixel_id="123456789012345", capi_token="EAAB-token", lead_value="40")
        assert c.put("/api/profile", json={"pixel_id": "abc"}).status_code == 400
        slug = me["slug"]
        c.cookies.set("_fbp", "fb.1.1700000000000.42")
        page = c.get(f"/f/{slug}/estimate?fbclid=IwAR_click_123456")
        assert "fbq('init','123456789012345'" in page.text.replace('"', "'")
        pv = next(e for e in captured if e["event_name"] == "PageView")
        assert f'eventID:"{pv["event_id"]}"' in page.text
        assert pv["user_data"]["fbp"] == "fb.1.1700000000000.42" and pv["user_data"]["fbc"].endswith("IwAR_click_123456")

        thanks = c.post(f"/f/{slug}/estimate", data={"name": "Jane Doe", "phone": "813-555-0199", "email": "jane@example.com",
                                                     "zip": "33602", "fbclid": "IwAR_click_123456"})
        lead = next(e for e in captured if e["event_name"] == "Lead")
        assert f'eventID:"{lead["event_id"]}"' in thanks.text          # same ID in browser and server = deduplicated
        assert lead["custom_data"]["value"] == 40.0 and lead["action_source"] == "website"
        assert "em" in lead["user_data"] and "ph" in lead["user_data"] and "zp" in lead["user_data"]
        assert "jane@example.com" not in json.dumps(lead)               # only hashes leave the server

        jane = next(x for x in c.get("/api/contacts").json() if x["name"] == "Jane Doe")
        c.patch(f"/api/contacts/{jane['id']}", json={"stage": "quoted"})
        c.patch(f"/api/contacts/{jane['id']}", json={"stage": "won", "value": 6400})
        names = [e["event_name"] for e in captured]
        assert "QualifiedLead" in names
        purchase = next(e for e in captured if e["event_name"] == "Purchase")
        assert purchase["custom_data"]["value"] == 6400.0 and purchase["action_source"] == "system_generated"
        assert purchase["user_data"]["fbc"].endswith("IwAR_click_123456")  # click ID remembered from the lead
        log = c.get("/api/tracking").json()["log"]
        assert log and all(x["status"] == "ok" for x in log)
        stats = c.get("/api/funnels").json()[0]["stats"]
        assert stats["views_7"] == 1 and stats["leads_7"] == 1


def test_no_pixel_no_tracking_code(captured):
    with TestClient(app) as c:
        me = new_owner(c)
        assert "fbevents.js" not in c.get(f"/f/{me['slug']}/estimate").text
        assert captured == []
        assert c.post("/api/tracking/test").status_code == 400
