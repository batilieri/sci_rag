from app.core.cache import build_cache_key, normalize_message, ttl_for_confidence


def test_normalize_message_removes_accents_punctuation_and_case():
    assert normalize_message("  Op\u00e7\u00e3o K300/K315 est\u00e1 CINZA!!! ") == "opcao k300 k315 esta cinza"


def test_build_cache_key_is_stable_after_normalization():
    a = build_cache_key("Op\u00e7\u00e3o K300/K315 est\u00e1 CINZA!", "Contabil", "suporte")
    b = build_cache_key("opcao k300 k315 esta cinza", "Contabil", "suporte")

    assert a == b
    assert a.startswith("rag:query:")


def test_ttl_for_confidence_bands():
    assert ttl_for_confidence(0.95) == 86400
    assert ttl_for_confidence(0.75) == 21600
    assert ttl_for_confidence(0.10) is None
