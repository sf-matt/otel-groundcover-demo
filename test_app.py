from app import app


client = app.test_client()


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json["status"] == "healthy"


def test_work():
    response = client.get("/work?workload=test")
    assert response.status_code == 200
    assert response.json["workload"] == "test"


def test_failure_routes_are_triggerable():
    assert client.get("/fail/http-500").status_code == 500
    assert client.get("/fail/exception").status_code == 500
    assert client.get("/fail/timeout?seconds=0").status_code == 504
    assert client.get("/fail/downstream").status_code == 502
