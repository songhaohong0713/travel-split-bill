def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_verifies_database_connectivity(client):
    assert client.get("/readyz").json() == {"status": "ready"}