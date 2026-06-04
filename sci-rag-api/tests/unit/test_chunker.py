from app.ingestion.chunker import build_image_chunk, build_text_chunks, image_chunk_text_for_embedding


def test_build_text_chunks_creates_parent_and_children():
    structured = {
        "faq_id": "7085",
        "titulo": "Balanco K300",
        "secoes": [
            {
                "titulo_secao": "Procedimento",
                "chunk_tipo": "procedimento",
                "texto_original": "Acesse Relatorios > Balanco patrimonial.\n\nSelecione Grupo economico.",
                "texto_enriquecido_para_embedding": "Balanco patrimonial K300 K315 Grupo economico.",
                "registros_sped_mencionados": ["K300", "K315"],
                "menus_caminhos": ["Relatorios > Balanco patrimonial"],
                "palavras_chave_exatas": ["esmaecido"],
            }
        ],
    }

    chunks = build_text_chunks(structured)

    assert chunks[0].chunk_id == "faq_7085_parent"
    assert chunks[1].faq_id == "7085"
    assert chunks[1].parent_chunk_id == "faq_7085_parent"
    assert chunks[1].payload_extra["registros_sped_mencionados"] == ["K300", "K315"]


def test_build_image_chunk_payload_for_embedding():
    chunk = build_image_chunk(
        faq_id="7085",
        image_asset_id="img_faq_7085_01",
        description_json={
            "titulo_janela": "Relatorio balanco patrimonial",
            "descricao_vision_llm": "Checkbox K300/K315 destacado.",
            "quando_enviar": ["quando perguntar onde marcar K300"],
        },
        storage={"bucket": "rag-images", "key": "sci/faq/7085/images/img_faq_7085_01.png"},
        filename="img.png",
        tamanho_bytes=123,
        width=800,
        height=600,
        hash_md5="abc",
    )

    text = image_chunk_text_for_embedding(chunk.payload_extra)

    assert chunk.payload_extra["image_asset_id"] == "img_faq_7085_01"
    assert chunk.payload_extra["r2_key"] == "sci/faq/7085/images/img_faq_7085_01.png"
    assert "K300" in text
