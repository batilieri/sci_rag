CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS rag_api_keys (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  key_id VARCHAR(40) UNIQUE NOT NULL,
  key_hash VARCHAR(64) UNIQUE NOT NULL,
  nome VARCHAR(200) NOT NULL,
  escopos JSONB NOT NULL DEFAULT '[]',
  ativo BOOLEAN NOT NULL DEFAULT true,
  rate_limit_override INTEGER NULL,
  ultimo_uso BIGINT NULL,
  revogada_em TIMESTAMPTZ NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rag_image_assets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  image_id VARCHAR(120) UNIQUE NOT NULL,
  faq_id VARCHAR(32) NOT NULL,
  source_document_id UUID NULL,
  chunk_id VARCHAR(160) NULL,
  original_filename VARCHAR(255) NULL,
  r2_bucket VARCHAR(120) NOT NULL,
  r2_key TEXT NOT NULL,
  r2_public_url TEXT NULL,
  r2_etag VARCHAR(160) NULL,
  content_type VARCHAR(80) NOT NULL DEFAULT 'image/png',
  tamanho_bytes BIGINT NOT NULL,
  width INTEGER NULL,
  height INTEGER NULL,
  hash_sha256 CHAR(64) NOT NULL,
  hash_md5 CHAR(32) NULL,
  ordem_no_faq INTEGER NULL,
  tipo_tela VARCHAR(80) NULL,
  titulo_janela TEXT NULL,
  descricao_curta TEXT NULL,
  menu_caminho_inferido TEXT NULL,
  registros_sped_visiveis JSONB NOT NULL DEFAULT '[]',
  palavras_chave_exatas JSONB NOT NULL DEFAULT '[]',
  quando_enviar JSONB NOT NULL DEFAULT '[]',
  revisado_humano BOOLEAN NOT NULL DEFAULT false,
  status VARCHAR(40) NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (r2_bucket, r2_key)
);

CREATE TABLE IF NOT EXISTS rag_ingestion_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id VARCHAR(64) UNIQUE NOT NULL,
  documento VARCHAR(255) NOT NULL,
  storage_path TEXT NULL,
  tamanho_bytes BIGINT NOT NULL DEFAULT 0,
  detected_pages INTEGER NULL,
  api_key_id UUID NULL REFERENCES rag_api_keys(id) ON DELETE SET NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'queued',
  phase VARCHAR(40) NOT NULL DEFAULT 'upload',
  progresso_pct DOUBLE PRECISION NOT NULL DEFAULT 0.0,
  faqs_detectados INTEGER NOT NULL DEFAULT 0,
  faqs_ingeridos INTEGER NOT NULL DEFAULT 0,
  imagens_extraidas INTEGER NOT NULL DEFAULT 0,
  imagens_upadas INTEGER NOT NULL DEFAULT 0,
  chunks_gerados INTEGER NOT NULL DEFAULT 0,
  chunks_upsertados INTEGER NOT NULL DEFAULT 0,
  submitted_at TIMESTAMPTZ NULL,
  started_at TIMESTAMPTZ NULL,
  finished_at TIMESTAMPTZ NULL,
  duracao_ms INTEGER NOT NULL DEFAULT 0,
  errors JSONB NULL,
  extras JSONB NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rag_query_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id VARCHAR(64) UNIQUE NOT NULL,
  api_key_id UUID NULL REFERENCES rag_api_keys(id) ON DELETE SET NULL,
  cliente_id_externo VARCHAR(120) NULL,
  conversa_id_externo VARCHAR(120) NULL,
  canal VARCHAR(32) NULL,
  departamento_atual VARCHAR(64) NULL,
  mensagem_normalizada_hash VARCHAR(64) NULL,
  mensagem_preview VARCHAR(200) NULL,
  acao VARCHAR(40) NOT NULL,
  motivo_transbordo VARCHAR(40) NULL,
  departamento_sugerido VARCHAR(64) NULL,
  confianca DOUBLE PRECISION NOT NULL DEFAULT 0.0,
  intencao_detectada VARCHAR(255) NULL,
  modelo_usado VARCHAR(64) NULL,
  cache_hit BOOLEAN NOT NULL DEFAULT false,
  tempo_total_ms INTEGER NOT NULL DEFAULT 0,
  tempo_busca_ms INTEGER NOT NULL DEFAULT 0,
  tempo_rerank_ms INTEGER NOT NULL DEFAULT 0,
  tempo_llm_ms INTEGER NOT NULL DEFAULT 0,
  tokens_entrada INTEGER NOT NULL DEFAULT 0,
  tokens_saida INTEGER NOT NULL DEFAULT 0,
  custo_estimado_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
  top_score_busca DOUBLE PRECISION NULL,
  faqs_consultados JSONB NULL,
  guardrails_acionados JSONB NULL,
  erros JSONB NULL,
  extras JSONB NULL,
  answer_preview TEXT NULL,
  finished_at TIMESTAMPTZ NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rag_feedback (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id VARCHAR(64) NOT NULL,
  tipo VARCHAR(20) NOT NULL,
  fonte VARCHAR(20) NOT NULL,
  comentario TEXT NULL,
  correcao_sugerida TEXT NULL,
  api_key_id UUID NULL REFERENCES rag_api_keys(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_rag_api_keys_key_id ON rag_api_keys(key_id);
CREATE INDEX IF NOT EXISTS ix_rag_api_keys_key_hash ON rag_api_keys(key_hash);
CREATE INDEX IF NOT EXISTS ix_rag_image_assets_faq_id ON rag_image_assets(faq_id);
CREATE INDEX IF NOT EXISTS ix_rag_image_assets_hash_sha256 ON rag_image_assets(hash_sha256);
CREATE INDEX IF NOT EXISTS ix_rag_image_assets_status ON rag_image_assets(status);
CREATE INDEX IF NOT EXISTS ix_rag_ingestion_jobs_job_id ON rag_ingestion_jobs(job_id);
CREATE INDEX IF NOT EXISTS ix_rag_query_logs_request_id ON rag_query_logs(request_id);
CREATE INDEX IF NOT EXISTS ix_rag_query_logs_created_at ON rag_query_logs(created_at);
CREATE INDEX IF NOT EXISTS ix_rag_query_logs_acao ON rag_query_logs(acao);
CREATE INDEX IF NOT EXISTS ix_rag_feedback_request_id ON rag_feedback(request_id);
CREATE INDEX IF NOT EXISTS ix_rag_feedback_tipo ON rag_feedback(tipo);

DROP TRIGGER IF EXISTS trg_rag_api_keys_updated_at ON rag_api_keys;
CREATE TRIGGER trg_rag_api_keys_updated_at BEFORE UPDATE ON rag_api_keys
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_rag_image_assets_updated_at ON rag_image_assets;
CREATE TRIGGER trg_rag_image_assets_updated_at BEFORE UPDATE ON rag_image_assets
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_rag_ingestion_jobs_updated_at ON rag_ingestion_jobs;
CREATE TRIGGER trg_rag_ingestion_jobs_updated_at BEFORE UPDATE ON rag_ingestion_jobs
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_rag_query_logs_updated_at ON rag_query_logs;
CREATE TRIGGER trg_rag_query_logs_updated_at BEFORE UPDATE ON rag_query_logs
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_rag_feedback_updated_at ON rag_feedback;
CREATE TRIGGER trg_rag_feedback_updated_at BEFORE UPDATE ON rag_feedback
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
