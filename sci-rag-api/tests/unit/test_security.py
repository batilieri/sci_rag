from app.core.security import RequiredScope, WebhookSigner, _has_scope, generate_raw_key, hash_api_key


def test_api_key_hash_is_sha256_and_raw_not_equal():
    raw, digest = generate_raw_key()

    assert raw.startswith("rag_live_")
    assert digest == hash_api_key(raw)
    assert digest != raw
    assert len(digest) == 64


def test_api_key_scope_hierarchy():
    assert _has_scope(["admin:*"], RequiredScope.ADMIN_WRITE)
    assert _has_scope(["admin:write"], RequiredScope.ADMIN_READ)
    assert not _has_scope(["query"], RequiredScope.FEEDBACK)


def test_webhook_hmac_sign_and_verify():
    signer = WebhookSigner("x" * 32)
    body = b'{"ok":true}'
    headers = signer.sign(body, timestamp=1_800_000_000)

    assert signer.verify(body, headers["X-RAG-Signature"], headers["X-RAG-Timestamp"], tolerance_sec=10**9)
    assert not signer.verify(b"{}", headers["X-RAG-Signature"], headers["X-RAG-Timestamp"], tolerance_sec=10**9)
