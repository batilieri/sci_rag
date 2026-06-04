import pytest
from pydantic import ValidationError

from app.schemas.common import Acao
from app.schemas.query import MensagemSaidaTexto, MetricasResposta, QueryRequest, QueryResponse


def test_query_request_validates_minimal_payload():
    payload = QueryRequest.model_validate(
        {
            "mensagem": "como marco K300?",
            "cliente": {"id_externo": "cli_1"},
            "conversa": {"id_externo": "conv_1", "historico": []},
        }
    )

    assert payload.opcoes.max_imagens == 3
    assert payload.conversa.canal.value == "whatsapp"


def test_query_request_rejects_blank_message():
    with pytest.raises(ValidationError):
        QueryRequest.model_validate(
            {"mensagem": "   ", "cliente": {"id_externo": "cli"}, "conversa": {"id_externo": "conv"}}
        )


def test_query_response_shape():
    response = QueryResponse(
        acao=Acao.TRANSFERIR_HUMANO,
        confianca=0.0,
        mensagens=[MensagemSaidaTexto(ordem=0, conteudo="Vou transferir.")],
        metricas=MetricasResposta(tempo_total_ms=1, modelo_usado="-"),
    )

    assert response.request_id.startswith("req_")
    assert response.mensagens[0].tipo.value == "texto"
