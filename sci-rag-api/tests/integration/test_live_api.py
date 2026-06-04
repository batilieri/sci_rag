import os

import httpx
import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="requires live docker stack")
def test_health_live():
    base_url = os.getenv("RAG_API_URL", "http://127.0.0.1:8000").rstrip("/")
    response = httpx.get(f"{base_url}/v1/health", timeout=10)

    assert response.status_code == 200
    assert response.json()["status"] in {"ok", "degraded", "down"}


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="requires live docker stack")
def test_query_live_transfers_out_of_scope():
    api_key = os.environ["RAG_API_KEY"]
    base_url = os.getenv("RAG_API_URL", "http://127.0.0.1:8000").rstrip("/")
    response = httpx.post(
        f"{base_url}/v1/query",
        headers={"X-API-Key": api_key},
        json={
            "mensagem": "me conta uma piada",
            "cliente": {"id_externo": "test"},
            "conversa": {"id_externo": "test", "historico": []},
        },
        timeout=20,
    )

    assert response.status_code == 200
    assert response.json()["acao"] == "TRANSFERIR_HUMANO"
