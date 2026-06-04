import asyncio

from app.rag.query_rewriter import rewrite_query


def test_query_rewriter_falls_back_without_deepseek_key():
    result = asyncio.run(rewrite_query("opcao K300 esta cinza", []))

    assert result.variantes == ["opcao K300 esta cinza"]
    assert "opcao" in result.termos_chave
