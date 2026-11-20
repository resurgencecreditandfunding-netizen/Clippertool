import itertools

from fastapi.testclient import TestClient

from standout.main import app

_n = itertools.count(9000)


def test_launch_kit_is_filled_in_for_the_business():
    with TestClient(app) as c:
        c.post("/api/signup", json={"email": f"l{next(_n)}@example.com", "password": "password123", "business": "Rapid Rooter"})
        c.put("/api/profile", json={"trade": "plumbing", "city": "Tampa", "phone": "8135550100", "services": "drains",
                                    "offer": "$75 off", "pixel_id": "123456789012345", "capi_token": "x"})
        k = c.get("/api/launch/estimate").json()
        flat = {i["label"]: i for s in k["steps"] for i in s["items"]}
        assert flat["Campaign objective"]["value"] == "Leads"
        assert flat["Pixel / dataset"]["value"] == "123456789012345"
        assert flat["Website URL"]["value"].endswith("/estimate?utm_source=facebook&utm_medium=paid&utm_campaign=estimate")
        assert "Tampa" in flat["Primary text"]["value"] and flat["Primary text"]["copy"]
        assert flat["Location"]["value"].startswith("Tampa")
        checks = {x["label"]: x["ok"] for x in k["checks"]}
        assert checks["Pixel + Conversions API connected"] and checks["Funnel page is turned on"]
        assert not checks["Competitor-edge promises confirmed"]
        assert c.get("/api/launch/nope").status_code == 404
