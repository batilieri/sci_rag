"""
Engine RAG — orquestrador do pipeline completo.

Localização: app/rag/engine.py

Recebe QueryRequest, executa todas as etapas, retorna QueryResponse.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator

from app.config import Settings
from app.rag.embeddings import EmbeddingService
from app.rag.generation import GenerationService
from app.rag.guardrails import GuardrailService, GuardrailVerdict
from app.rag.query_rewriter import QueryRewriter
from app.rag.reranker import RerankerService
from app.rag.retrieval import ChunkRecuperado, RetrievalService
from app.schemas.query import (
    Acao,
    FAQConsultado,
    MensagemSaidaImagem,
    MensagemSaidaTexto,
    MetricasResposta,
    MotivoTransbordo,
    QueryRequest,
    QueryResponse,
    DebugInfo,
)

log = logging.getLogger(__name__)


class RAGEngineError(Exception):
    """Erro recuperável do engine — vira 503 no endpoint."""
    def __init__(self, msg: str, componente: str):
        super().__init__(msg)
        self.componente = componente


@dataclass
class PipelineTiming:
    busca_ms: int = 0
    rerank_ms: int = 0
    llm_ms: int = 0


class RAGEngine:
    """
    Singleton injetado em app.state.rag_engine.
    Mantém em memória: BGE-M3 model, clientes Qdrant/Redis/LLMs.
    """

    def __init__(
        self,
        settings: Settings,
        embeddings: EmbeddingService,
        retrieval: RetrievalService,
        reranker: RerankerService,
        rewriter: QueryRewriter,
        generation: GenerationService,
        guardrails: GuardrailService,
    ):
        self.settings = settings
        self.embeddings = embeddings
        self.retrieval = retrieval
        self.reranker = reranker
        self.rewriter = rewriter
        self.generation = generation
        self.guardrails = guardrails

    # ═══════════════════════════════════════════════════════════════
    # PIPELINE PRINCIPAL
    # ═══════════════════════════════════════════════════════════════

    async def process(self, req: QueryRequest) -> QueryResponse:
        timing = PipelineTiming()

        # ── Etapa 1: Reescrever query (gera variantes para melhor recall)
        queries = await self.rewriter.rewrite(
            query=req.mensagem,
            historico=req.conversa.historico,
        )
        log.info("Queries reescritas: %s", queries)

        # ── Etapa 2: Busca híbrida no Qdrant
        t = time.perf_counter()
        try:
            candidatos = await self.retrieval.search_hybrid(
                queries=queries,
                filtros_categoria=req.opcoes.filtros_categoria,
                limit=20,
            )
        except Exception as e:
            log.exception("Erro na busca Qdrant")
            raise RAGEngineError(str(e), componente="qdrant")
        timing.busca_ms = int((time.perf_counter() - t) * 1000)

        if not candidatos:
            return self._build_transbordo_response(
                req=req,
                motivo=MotivoTransbordo.OUT_OF_SCOPE,
                timing=timing,
            )

        # ── Etapa 3: Reranking ColBERT (alta precisão)
        t = time.perf_counter()
        try:
            finalists = await self.reranker.rerank(
                query=req.mensagem,
                candidatos=candidatos,
                top_k=5,
            )
        except Exception as e:
            log.warning("Reranker falhou, usando candidatos originais: %s", e)
            finalists = candidatos[:5]
        timing.rerank_ms = int((time.perf_counter() - t) * 1000)

        # ── Guardrail 1: score do top chunk
        top_score = finalists[0].score if finalists else 0.0
        threshold = req.opcoes.threshold_confianca_minima or self.settings.MIN_SCORE_TOP_CHUNK
        if top_score < threshold:
            log.info("Top score %.3f < threshold %.3f → transbordo", top_score, threshold)
            return self._build_transbordo_response(
                req=req,
                motivo=MotivoTransbordo.LOW_RETRIEVAL_SCORE,
                timing=timing,
                faqs_consultados=self._build_faqs_consultados(finalists),
            )

        # ── Etapa 4: Separar chunks de texto vs imagem
        chunks_texto = [c for c in finalists if c.payload.get("tipo_chunk") == "texto"]
        chunks_imagem = [c for c in finalists if c.payload.get("tipo_chunk") == "imagem"]

        # ── Etapa 5: Escolher modelo (DeepSeek default, Sonnet para casos complexos)
        modelo_escolhido = self._escolher_modelo(req, finalists)

        # ── Etapa 6: Gerar resposta com LLM
        t = time.perf_counter()
        try:
            llm_output = await self.generation.generate(
                req=req,
                chunks_texto=chunks_texto,
                chunks_imagem=chunks_imagem,
                modelo=modelo_escolhido,
            )
        except Exception as e:
            log.exception("Erro na geração LLM")
            raise RAGEngineError(str(e), componente="llm")
        timing.llm_ms = int((time.perf_counter() - t) * 1000)

        # ── Etapa 7: Aplicar guardrails
        verdict: GuardrailVerdict = await self.guardrails.evaluate(
            req=req,
            llm_output=llm_output,
            chunks=finalists,
        )

        if not verdict.aprovado:
            log.warning("Guardrail bloqueou resposta: %s", verdict.motivo)
            return self._build_transbordo_response(
                req=req,
                motivo=verdict.motivo,
                timing=timing,
                faqs_consultados=self._build_faqs_consultados(finalists),
                modelo_usado=modelo_escolhido,
                custo_usd=llm_output.custo_usd,
                tokens=(llm_output.tokens_entrada, llm_output.tokens_saida),
            )

        # ── Etapa 8: Montar mensagens de saída intercalando texto e imagem
        mensagens_saida = self._build_mensagens_saida(
            llm_output=llm_output,
            chunks_imagem=chunks_imagem,
            max_imagens=req.opcoes.max_imagens,
        )

        # ── Etapa 9: Resposta final
        return QueryResponse(
            acao=Acao.RESPONDER,
            confianca=llm_output.confianca,
            departamento_sugerido=llm_output.departamento_sugerido,
            motivo_transbordo=None,
            intencao_detectada=llm_output.intencao_detectada,
            necessita_followup=llm_output.necessita_followup,
            mensagens=mensagens_saida,
            faqs_consultados=self._build_faqs_consultados(finalists),
            metricas=MetricasResposta(
                tempo_total_ms=0,  # preenchido no endpoint
                tempo_busca_ms=timing.busca_ms,
                tempo_rerank_ms=timing.rerank_ms,
                tempo_llm_ms=timing.llm_ms,
                tokens_entrada=llm_output.tokens_entrada,
                tokens_saida=llm_output.tokens_saida,
                custo_estimado_usd=llm_output.custo_usd,
                modelo_usado=modelo_escolhido,
            ),
            debug=DebugInfo(
                queries_reescritas=queries,
                top_chunks=[
                    {"id": c.point_id, "score": c.score, "faq_id": c.payload.get("faq_id")}
                    for c in finalists[:5]
                ],
                raciocinio_llm=llm_output.raciocinio_interno,
                guardrails_acionados=verdict.guardrails_acionados,
                embedding_modelo="BAAI/bge-m3",
            ) if req.opcoes.incluir_debug else None,
        )

    # ═══════════════════════════════════════════════════════════════
    # STREAMING (SSE)
    # ═══════════════════════════════════════════════════════════════

    async def process_streaming(self, req: QueryRequest) -> AsyncIterator[dict]:
        """
        Versão streaming. Emite eventos conforme cada etapa completa.
        Reimplementa process() com yields nos pontos relevantes.
        """
        yield {"type": "retrieval_started", "data": "{}"}
        # ... (similar a process(), com yields entre etapas)
        # Omitido para brevidade — implementar quando o painel web precisar

    # ═══════════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════════

    def _escolher_modelo(self, req: QueryRequest, finalists: list[ChunkRecuperado]) -> str:
        """
        Heurística: DeepSeek para casos simples, Sonnet para complexos.
        """
        from app.schemas.query import ModeloLLM

        if req.opcoes.modelo_preferido != ModeloLLM.AUTO:
            return req.opcoes.modelo_preferido.value

        # Critérios para usar Sonnet (mais caro, mais capaz)
        msg_longa = len(req.mensagem.split()) > 30
        score_baixo = finalists and finalists[0].score < 0.80
        historico_longo = len(req.conversa.historico) > 6
        multiplos_topicos = len(set(c.payload.get("faq_id") for c in finalists[:5])) > 2

        if msg_longa or score_baixo or historico_longo or multiplos_topicos:
            return ModeloLLM.CLAUDE_SONNET_45.value

        return ModeloLLM.DEEPSEEK_V4_PRO.value

    def _build_faqs_consultados(self, chunks: list[ChunkRecuperado]) -> list[FAQConsultado]:
        """Deduplica por faq_id e agrega scores."""
        seen: dict[str, FAQConsultado] = {}
        for c in chunks:
            faq_id = c.payload.get("faq_id")
            if not faq_id:
                continue
            if faq_id not in seen:
                seen[faq_id] = FAQConsultado(
                    faq_id=faq_id,
                    titulo=c.payload.get("faq_titulo", ""),
                    score=c.score,
                    url_original=c.payload.get("fonte", {}).get("url_original"),
                    chunks_usados=[c.point_id],
                )
            else:
                seen[faq_id].chunks_usados.append(c.point_id)
                seen[faq_id].score = max(seen[faq_id].score, c.score)

        return sorted(seen.values(), key=lambda f: f.score, reverse=True)

    def _build_mensagens_saida(
        self,
        llm_output,
        chunks_imagem: list[ChunkRecuperado],
        max_imagens: int,
    ):
        """
        Intercala mensagens de texto com imagens conforme `ordem_no_envio`
        que o LLM definiu na saída.
        """
        mensagens = []

        # Mensagens de texto do LLM
        for i, texto in enumerate(llm_output.mensagens):
            mensagens.append(MensagemSaidaTexto(ordem=i, conteudo=texto))

        # Inserir imagens nas posições corretas
        imagens_indicadas = llm_output.imagens_a_enviar[:max_imagens]
        for img_info in imagens_indicadas:
            # Encontrar chunk de imagem correspondente
            chunk_img = next(
                (c for c in chunks_imagem if c.point_id == img_info["imagem_id"]),
                None
            )
            if not chunk_img:
                continue

            # Reordenar: imagens com ordem_no_envio entram na posição certa
            posicao = img_info["ordem_no_envio"]
            # Ajustar ordens das mensagens posteriores
            for m in mensagens:
                if m.ordem >= posicao:
                    m.ordem += 1

            mensagens.append(MensagemSaidaImagem(
                ordem=posicao,
                url=chunk_img.payload.get("storage_url", ""),
                legenda=img_info.get("legenda"),
                mime_type="image/png",
                largura=chunk_img.payload.get("dimensoes", {}).get("width"),
                altura=chunk_img.payload.get("dimensoes", {}).get("height"),
                tamanho_bytes=chunk_img.payload.get("tamanho_bytes"),
            ))

        return sorted(mensagens, key=lambda m: m.ordem)

    def _build_transbordo_response(
        self,
        req: QueryRequest,
        motivo: MotivoTransbordo,
        timing: PipelineTiming,
        faqs_consultados: list[FAQConsultado] | None = None,
        modelo_usado: str = "none",
        custo_usd: float = 0.0,
        tokens: tuple[int, int] = (0, 0),
    ) -> QueryResponse:
        """Resposta padronizada de transbordo para humano."""
        return QueryResponse(
            acao=Acao.TRANSFERIR_HUMANO,
            confianca=0.0,
            departamento_sugerido="suporte_contabil",  # default
            motivo_transbordo=motivo,
            intencao_detectada=None,
            necessita_followup=False,
            mensagens=[
                MensagemSaidaTexto(
                    ordem=0,
                    conteudo="Vou te transferir para um atendente humano que vai conseguir te ajudar melhor nesse caso. Só um momento."
                )
            ],
            faqs_consultados=faqs_consultados or [],
            metricas=MetricasResposta(
                tempo_total_ms=0,
                tempo_busca_ms=timing.busca_ms,
                tempo_rerank_ms=timing.rerank_ms,
                tempo_llm_ms=timing.llm_ms,
                tokens_entrada=tokens[0],
                tokens_saida=tokens[1],
                custo_estimado_usd=custo_usd,
                modelo_usado=modelo_usado,
            ),
        )
