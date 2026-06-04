"""RAG engine — orchestrates the full request lifecycle.

Flow (per the spec):
  1. PII scrubber + pre-LLM guardrails (out-of-scope, frustration, sensitive data).
  2. Cache lookup (unless opcoes.bypass_cache).
  3. Query rewriting.
  4. Hybrid retrieval (dense+sparse fusion).
  5. Retrieval guardrail (top_score >= threshold).
  6. Rerank top candidates.
  7. Build LLM prompt + generate JSON response.
  8. Post-LLM guardrails (hallucinated FAQ, confidence floor).
  9. Resolve image URLs from R2 / Postgres.
 10. Persist QueryLog + cache + dispatch webhooks.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.cache import build_cache_key, get_response_cache, ttl_for_confidence
from app.core.logging import get_logger
from app.core.webhooks import send_webhook_now
from app.models.image_asset import ImageAsset
from app.models.query_log import QueryLog
from app.rag import guardrails
from app.rag.generation import generate
from app.rag.query_rewriter import rewrite_query
from app.rag.reranker import rerank
from app.rag.retrieval import RetrievedChunk, partition_text_image, retrieve, top_score
from app.schemas.common import Acao, MotivoTransbordo, TipoMensagem
from app.schemas.query import (
    DebugInfo,
    FAQConsultado,
    MensagemSaidaImagem,
    MensagemSaidaTexto,
    MetricasResposta,
    QueryRequest,
    QueryResponse,
)
from app.storage import object_storage

logger = get_logger(__name__)


@dataclass(slots=True)
class _Phase:
    started: float = field(default_factory=time.perf_counter)
    busca_ms: int = 0
    rerank_ms: int = 0
    llm_ms: int = 0

    def stop_busca(self) -> None:
        self.busca_ms = int((time.perf_counter() - self.started) * 1000)
        self.started = time.perf_counter()

    def stop_rerank(self) -> None:
        self.rerank_ms = int((time.perf_counter() - self.started) * 1000)
        self.started = time.perf_counter()

    def stop_llm(self) -> None:
        self.llm_ms = int((time.perf_counter() - self.started) * 1000)


class RAGEngine:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.cache = get_response_cache()

    async def process(self, payload: QueryRequest, api_key_id: str | None) -> QueryResponse:
        request_start = time.perf_counter()
        settings = get_settings()
        request_id = f"req_{int(time.time()*1000):x}"
        triggered_names: list[str] = []
        cache_key = build_cache_key(
            payload.mensagem,
            payload.cliente.licenca_sci,
            payload.conversa.departamento_atual,
        )

        # ---- 1. pre-LLM guardrails ----
        pre = guardrails.check_pre_llm(payload.mensagem)
        triggered_names.extend(guardrails.names(pre))
        scrubbed_message = payload.mensagem
        for r in pre:
            if r.scrubbed_text:
                scrubbed_message = r.scrubbed_text

        block = guardrails.first_blocking(pre)
        if block:
            response = self._build_transfer_response(
                request_id=request_id,
                motivo=block.motivo or MotivoTransbordo.OUT_OF_SCOPE,
                mensagem=block.mensagem_para_cliente or self._default_transfer_message(),
                departamento=block.departamento_sugerido or payload.conversa.departamento_atual or "suporte_contabil",
                elapsed_ms=self._elapsed_ms(request_start),
                guardrails_triggered=triggered_names,
                modelo="-",
            )
            await self._persist_log(response, payload, api_key_id, cache_key, top_score_busca=None)
            await self._dispatch_transfer_webhook(response, payload)
            return response

        # ---- 1b. saudação/smalltalk: responde na hora, sem rodar RAG ----
        if guardrails.detect_greeting(scrubbed_message):
            response = self._build_greeting_response(
                request_id=request_id,
                elapsed_ms=self._elapsed_ms(request_start),
            )
            await self._persist_log(response, payload, api_key_id, cache_key, top_score_busca=None)
            return response

        # ---- 2. cache ----
        if not payload.opcoes.bypass_cache:
            cached = await self.cache.get(cache_key)
            if cached:
                cached["request_id"] = request_id
                cached.setdefault("metricas", {})["cache_hit"] = True
                cached["metricas"]["tempo_total_ms"] = self._elapsed_ms(request_start)
                response = QueryResponse.model_validate(cached)
                await self._persist_log(response, payload, api_key_id, cache_key, top_score_busca=None)
                return response

        # ---- 3. query rewriting ----
        rewrite = await rewrite_query(scrubbed_message, payload.conversa.historico)
        variantes = rewrite.variantes or [scrubbed_message]

        # ---- 4. retrieval ----
        phase = _Phase()
        chunks = await retrieve(
            queries=variantes,
            top_k=settings.retrieval_top_k,
            category_filter=payload.opcoes.filtros_categoria,
        )
        phase.stop_busca()
        score_top = top_score(chunks)

        # ---- 5. retrieval guardrail (no-results only; relevance is judged after rerank) ----
        retr_guards = guardrails.check_no_results(chunks)
        triggered_names.extend(guardrails.names(retr_guards))
        block = guardrails.first_blocking(retr_guards)
        if block:
            response = self._build_transfer_response(
                request_id=request_id,
                motivo=block.motivo or MotivoTransbordo.NO_RESULTS,
                mensagem=block.mensagem_para_cliente or self._default_transfer_message(),
                departamento=block.departamento_sugerido or "suporte_contabil",
                elapsed_ms=self._elapsed_ms(request_start),
                guardrails_triggered=triggered_names,
                modelo="-",
                metricas_extra={"tempo_busca_ms": phase.busca_ms},
            )
            await self._persist_log(response, payload, api_key_id, cache_key, top_score_busca=score_top)
            await self._dispatch_transfer_webhook(response, payload)
            return response

        # ---- 6. rerank ----
        # Cross-encoder reranking is CPU-bound (~2-3s/candidate), so only rerank the
        # best shortlist from retrieval instead of every fused candidate. `chunks` is
        # already sorted by retrieval score desc.
        rerank_input = chunks[: settings.rerank_max_candidates]
        reranked = await rerank(
            scrubbed_message, rerank_input, top_k=settings.rerank_top_k
        )
        phase.stop_rerank()

        # Normalized (0-1) top relevance from the reranker; fall back to passing the
        # gate if the reranker was unavailable (chunks still exist; post-LLM
        # confidence guardrail remains the backstop).
        rerank_scores = [c.rerank_score for c in reranked if c.rerank_score is not None]
        rerank_top = max(rerank_scores) if rerank_scores else 1.0

        # ---- 6b. relevance guardrail (on the normalized rerank score) ----
        rel_guards = guardrails.check_relevance(rerank_top)
        triggered_names.extend(guardrails.names(rel_guards))
        block = guardrails.first_blocking(rel_guards)
        if block:
            response = self._build_transfer_response(
                request_id=request_id,
                motivo=block.motivo or MotivoTransbordo.LOW_RETRIEVAL_SCORE,
                mensagem=block.mensagem_para_cliente or self._default_transfer_message(),
                departamento=block.departamento_sugerido or "suporte_contabil",
                elapsed_ms=self._elapsed_ms(request_start),
                guardrails_triggered=triggered_names,
                modelo="-",
                metricas_extra={"tempo_busca_ms": phase.busca_ms, "tempo_rerank_ms": phase.rerank_ms},
            )
            await self._persist_log(response, payload, api_key_id, cache_key, top_score_busca=score_top)
            await self._dispatch_transfer_webhook(response, payload)
            return response

        text_chunks, image_chunks = partition_text_image(reranked)

        # ---- 7. generation ----
        gen = await generate(
            pergunta=scrubbed_message,
            cliente=payload.cliente,
            historico=payload.conversa.historico,
            chunks=reranked,
            queries_reescritas=variantes,
            requested_model=payload.opcoes.modelo_preferido.value,
            top_score=rerank_top,
        )
        phase.stop_llm()

        # ---- 8. post-LLM guardrails ----
        post = guardrails.check_post_llm(gen.parsed, reranked)
        triggered_names.extend(guardrails.names(post))
        block = guardrails.first_blocking(post)
        if block:
            response = self._build_transfer_response(
                request_id=request_id,
                motivo=block.motivo or MotivoTransbordo.HALLUCINATION_DETECTED,
                mensagem=block.mensagem_para_cliente or self._default_transfer_message(),
                departamento=block.departamento_sugerido or "suporte_contabil",
                elapsed_ms=self._elapsed_ms(request_start),
                guardrails_triggered=triggered_names,
                modelo=gen.model_used,
                metricas_extra={
                    "tempo_busca_ms": phase.busca_ms,
                    "tempo_rerank_ms": phase.rerank_ms,
                    "tempo_llm_ms": gen.duracao_ms,
                    "tokens_entrada": gen.tokens_input,
                    "tokens_saida": gen.tokens_output,
                    "custo_estimado_usd": gen.custo_usd,
                },
            )
            await self._persist_log(response, payload, api_key_id, cache_key, top_score_busca=score_top)
            await self._dispatch_transfer_webhook(response, payload)
            return response

        # ---- 9. assemble response ----
        response = await self._assemble_response(
            request_id=request_id,
            parsed=gen.parsed,
            chunks=reranked,
            image_chunks=image_chunks,
            max_imagens=payload.opcoes.max_imagens,
            metricas=MetricasResposta(
                tempo_total_ms=self._elapsed_ms(request_start),
                tempo_busca_ms=phase.busca_ms,
                tempo_rerank_ms=phase.rerank_ms,
                tempo_llm_ms=gen.duracao_ms,
                tokens_entrada=gen.tokens_input,
                tokens_saida=gen.tokens_output,
                custo_estimado_usd=gen.custo_usd,
                modelo_usado=gen.model_used,
                cache_hit=False,
            ),
            include_debug=payload.opcoes.incluir_debug,
            debug_payload={
                "queries_reescritas": variantes,
                "top_chunks": [
                    {"chunk_id": c.chunk_id, "score": c.score, "rerank_score": c.rerank_score}
                    for c in reranked
                ],
                "raciocinio_llm": gen.parsed.get("raciocinio"),
                "guardrails_acionados": triggered_names,
                "embedding_modelo": settings.embedding_model,
            },
        )

        # ---- 10. persist + cache + webhook ----
        await self._persist_log(response, payload, api_key_id, cache_key, top_score_busca=score_top)

        if response.acao == Acao.RESPONDER:
            ttl = ttl_for_confidence(response.confianca)
            if ttl:
                cacheable = response.model_dump(mode="json")
                cacheable.pop("request_id", None)
                await self.cache.set(cache_key, cacheable, ttl_seconds=ttl)

        if response.confianca < 0.80 and response.acao == Acao.RESPONDER:
            await send_webhook_now(
                "query.low_confidence",
                {
                    "request_id": response.request_id,
                    "confianca": response.confianca,
                    "cliente_id_externo": payload.cliente.id_externo,
                    "conversa_id_externo": payload.conversa.id_externo,
                    "intencao_detectada": response.intencao_detectada,
                },
                request_id=response.request_id,
            )

        return response

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _elapsed_ms(start: float) -> int:
        return int((time.perf_counter() - start) * 1000)

    @staticmethod
    def _default_transfer_message() -> str:
        return (
            "Vou te transferir para um atendente humano que vai conseguir te ajudar melhor "
            "nesse caso. So um momento."
        )

    def _build_greeting_response(self, *, request_id: str, elapsed_ms: int) -> QueryResponse:
        """Resposta instantânea para saudações, sem custo de RAG/LLM."""
        return QueryResponse(
            request_id=request_id,
            acao=Acao.RESPONDER,
            confianca=1.0,
            departamento_sugerido=None,
            motivo_transbordo=None,
            intencao_detectada="saudacao",
            necessita_followup=True,
            mensagens=[MensagemSaidaTexto(ordem=0, conteudo=guardrails.GREETING_MESSAGE)],
            faqs_consultados=[],
            metricas=MetricasResposta(
                tempo_total_ms=elapsed_ms,
                modelo_usado="-",
                cache_hit=False,
            ),
        )

    def _build_transfer_response(
        self,
        *,
        request_id: str,
        motivo: MotivoTransbordo,
        mensagem: str,
        departamento: str,
        elapsed_ms: int,
        guardrails_triggered: list[str],
        modelo: str,
        metricas_extra: dict[str, Any] | None = None,
    ) -> QueryResponse:
        extras = metricas_extra or {}
        metricas = MetricasResposta(
            tempo_total_ms=elapsed_ms,
            tempo_busca_ms=int(extras.get("tempo_busca_ms", 0)),
            tempo_rerank_ms=int(extras.get("tempo_rerank_ms", 0)),
            tempo_llm_ms=int(extras.get("tempo_llm_ms", 0)),
            tokens_entrada=int(extras.get("tokens_entrada", 0)),
            tokens_saida=int(extras.get("tokens_saida", 0)),
            custo_estimado_usd=float(extras.get("custo_estimado_usd", 0.0)),
            modelo_usado=modelo,
            cache_hit=False,
        )
        return QueryResponse(
            request_id=request_id,
            acao=Acao.TRANSFERIR_HUMANO,
            confianca=0.0,
            departamento_sugerido=departamento,
            motivo_transbordo=motivo,
            intencao_detectada=None,
            necessita_followup=False,
            mensagens=[MensagemSaidaTexto(ordem=0, conteudo=mensagem)],
            faqs_consultados=[],
            metricas=metricas,
            debug=DebugInfo(guardrails_acionados=guardrails_triggered) if guardrails_triggered else None,
        )

    async def _assemble_response(
        self,
        *,
        request_id: str,
        parsed: dict[str, Any],
        chunks: list[RetrievedChunk],
        image_chunks: list[RetrievedChunk],
        max_imagens: int,
        metricas: MetricasResposta,
        include_debug: bool,
        debug_payload: dict[str, Any],
    ) -> QueryResponse:
        acao_str = parsed.get("acao") or Acao.TRANSFERIR_HUMANO.value
        try:
            acao = Acao(acao_str)
        except ValueError:
            acao = Acao.TRANSFERIR_HUMANO

        mensagens_out: list[Any] = []
        image_asset_ids_used: list[str] = []
        for idx, msg in enumerate(parsed.get("mensagens") or []):
            tipo = msg.get("tipo")
            if tipo == TipoMensagem.IMAGEM.value:
                if len(image_asset_ids_used) >= max_imagens:
                    continue
                resolved = await self._resolve_image_message(msg, image_chunks)
                if resolved is None:
                    continue
                mensagens_out.append(resolved)
                if resolved.url:
                    image_asset_ids_used.append(resolved.url)
            else:
                mensagens_out.append(
                    MensagemSaidaTexto(ordem=idx, conteudo=str(msg.get("conteudo") or "").strip())
                )

        if acao == Acao.RESPONDER and not mensagens_out:
            return self._build_transfer_response(
                request_id=request_id,
                motivo=MotivoTransbordo.INTERNAL_ERROR,
                mensagem=self._default_transfer_message(),
                departamento="suporte_contabil",
                elapsed_ms=metricas.tempo_total_ms,
                guardrails_triggered=debug_payload.get("guardrails_acionados", []),
                modelo=metricas.modelo_usado,
            )

        faqs = self._build_faqs_consultados(parsed.get("faqs_consultados") or [], chunks)

        confianca = float(parsed.get("confianca") or 0.0)
        if acao != Acao.RESPONDER:
            confianca = 0.0

        return QueryResponse(
            request_id=request_id,
            acao=acao,
            confianca=max(0.0, min(1.0, confianca)),
            departamento_sugerido=parsed.get("departamento_sugerido"),
            motivo_transbordo=self._parse_motivo(parsed.get("motivo_transbordo")),
            intencao_detectada=parsed.get("intencao_detectada"),
            necessita_followup=bool(parsed.get("necessita_followup", False)),
            mensagens=mensagens_out,
            faqs_consultados=faqs,
            metricas=metricas,
            debug=DebugInfo(**debug_payload) if include_debug else None,
        )

    @staticmethod
    def _parse_motivo(value: Any) -> MotivoTransbordo | None:
        if not value:
            return None
        try:
            return MotivoTransbordo(value)
        except ValueError:
            return None

    async def _resolve_image_message(
        self, msg: dict[str, Any], image_chunks: list[RetrievedChunk]
    ) -> MensagemSaidaImagem | None:
        image_asset_id = msg.get("image_asset_id") or msg.get("filename")
        legenda = msg.get("legenda")
        ordem = int(msg.get("ordem", 0))

        chunk_match: RetrievedChunk | None = None
        for c in image_chunks:
            if c.payload.get("image_asset_id") == image_asset_id or c.payload.get("filename") == image_asset_id:
                chunk_match = c
                break

        # Resolve via Postgres so we always get the latest URL/bucket.
        asset = None
        if image_asset_id:
            stmt = select(ImageAsset).where(ImageAsset.image_id == image_asset_id)
            asset = (await self.session.execute(stmt)).scalar_one_or_none()

        if asset is None and chunk_match is not None:
            bucket = chunk_match.payload.get("r2_bucket")
            key = chunk_match.payload.get("r2_key")
            public_url = chunk_match.payload.get("r2_public_url")
            if not bucket or not key:
                return None
            url = await object_storage.url_for_delivery(bucket, key, public_url=public_url)
            return MensagemSaidaImagem(
                ordem=ordem,
                url=url,
                legenda=legenda,
                mime_type=chunk_match.payload.get("content_type") or "image/png",
                largura=(chunk_match.payload.get("dimensoes") or {}).get("largura"),
                altura=(chunk_match.payload.get("dimensoes") or {}).get("altura"),
                tamanho_bytes=chunk_match.payload.get("tamanho_bytes"),
            )
        if asset is None:
            return None

        url = await object_storage.url_for_delivery(asset.r2_bucket, asset.r2_key, public_url=asset.r2_public_url)
        return MensagemSaidaImagem(
            ordem=ordem,
            url=url,
            legenda=legenda or asset.descricao_curta,
            mime_type=asset.content_type or "image/png",
            largura=asset.width,
            altura=asset.height,
            tamanho_bytes=asset.tamanho_bytes,
        )

    @staticmethod
    def _build_faqs_consultados(
        cited: list[dict[str, Any]],
        chunks: list[RetrievedChunk],
    ) -> list[FAQConsultado]:
        cited_index = {c.get("faq_id"): c.get("chunks_usados") or [] for c in cited if c.get("faq_id")}

        # Index chunks by faq_id for quick lookup.
        by_faq: dict[str, list[RetrievedChunk]] = {}
        for c in chunks:
            fid = c.faq_id
            if not fid:
                continue
            by_faq.setdefault(fid, []).append(c)

        result: list[FAQConsultado] = []
        for faq_id, chunk_ids in cited_index.items():
            relevant = by_faq.get(faq_id) or []
            top = relevant[0] if relevant else None
            score = max((c.rerank_score or c.score for c in relevant), default=0.0)
            result.append(
                FAQConsultado(
                    faq_id=faq_id,
                    titulo=(top.payload.get("faq_titulo") if top else None) or faq_id,
                    score=max(0.0, min(1.0, score)),
                    url_original=top.payload.get("fonte", {}).get("url_original") if top else None,
                    chunks_usados=list(chunk_ids),
                )
            )
        return result

    async def _persist_log(
        self,
        response: QueryResponse,
        payload: QueryRequest,
        api_key_id: str | None,
        cache_key: str,
        *,
        top_score_busca: float | None,
    ) -> None:
        try:
            row = QueryLog(
                request_id=response.request_id,
                api_key_id=None,  # FK resolution done in dependency layer if needed
                cliente_id_externo=payload.cliente.id_externo,
                conversa_id_externo=payload.conversa.id_externo,
                canal=payload.conversa.canal.value,
                departamento_atual=payload.conversa.departamento_atual,
                mensagem_normalizada_hash=cache_key.split(":")[-1],
                mensagem_preview=payload.mensagem[:200],
                acao=response.acao.value,
                motivo_transbordo=response.motivo_transbordo.value if response.motivo_transbordo else None,
                departamento_sugerido=response.departamento_sugerido,
                confianca=response.confianca,
                intencao_detectada=response.intencao_detectada,
                modelo_usado=response.metricas.modelo_usado,
                cache_hit=response.metricas.cache_hit,
                tempo_total_ms=response.metricas.tempo_total_ms,
                tempo_busca_ms=response.metricas.tempo_busca_ms,
                tempo_rerank_ms=response.metricas.tempo_rerank_ms,
                tempo_llm_ms=response.metricas.tempo_llm_ms,
                tokens_entrada=response.metricas.tokens_entrada,
                tokens_saida=response.metricas.tokens_saida,
                custo_estimado_usd=response.metricas.custo_estimado_usd,
                top_score_busca=top_score_busca,
                faqs_consultados=[f.model_dump() for f in response.faqs_consultados],
                guardrails_acionados=response.debug.guardrails_acionados if response.debug else [],
                answer_preview=self._answer_preview(response),
            )
            self.session.add(row)
            await self.session.commit()
        except Exception as exc:
            await self.session.rollback()
            logger.warning("query_log_persist_failed", error=str(exc))

    @staticmethod
    def _answer_preview(response: QueryResponse) -> str:
        for msg in response.mensagens:
            if msg.tipo == TipoMensagem.TEXTO:
                return msg.conteudo[:500]
        return ""

    @staticmethod
    async def _dispatch_transfer_webhook(response: QueryResponse, payload: QueryRequest) -> None:
        # Best-effort outbound webhook so SCI can update conversation state.
        await send_webhook_now(
            "query.transferred_human",
            {
                "request_id": response.request_id,
                "motivo": response.motivo_transbordo.value if response.motivo_transbordo else None,
                "departamento_sugerido": response.departamento_sugerido,
                "cliente_id_externo": payload.cliente.id_externo,
                "conversa_id_externo": payload.conversa.id_externo,
            },
            request_id=response.request_id,
        )


def get_engine(session: AsyncSession) -> RAGEngine:
    return RAGEngine(session)
