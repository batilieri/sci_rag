from app.rag.guardrails import check_post_llm, check_pre_llm, check_retrieval, first_blocking, scrub_pii
from app.schemas.common import MotivoTransbordo


class DummyChunk:
    def __init__(self, faq_id: str, score: float = 0.9):
        self.faq_id = faq_id
        self.score = score
        self.payload = {"faq_id": faq_id}


def test_scrub_pii_masks_cpf():
    clean, matched = scrub_pii("meu cpf e 123.456.789-10 e a duvida e K300")

    assert "[REDACTED]" in clean
    assert "cpf" in matched


def test_pre_llm_transfers_password_request():
    result = check_pre_llm("preciso recuperar minha senha do sistema")
    block = first_blocking(result)

    assert block is not None
    assert block.motivo == MotivoTransbordo.SENSITIVE_DATA_REQUEST


def test_retrieval_fails_closed_without_chunks():
    result = check_retrieval(0.0, [])

    assert first_blocking(result).motivo == MotivoTransbordo.NO_RESULTS


def test_post_llm_blocks_hallucinated_faq():
    result = check_post_llm(
        {"acao": "RESPONDER", "confianca": 0.9, "mensagens": [{"tipo": "texto"}], "faqs_consultados": [{"faq_id": "9999"}]},
        [DummyChunk("7085")],
    )

    assert first_blocking(result).motivo == MotivoTransbordo.HALLUCINATION_DETECTED
