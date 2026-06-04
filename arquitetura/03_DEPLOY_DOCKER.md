# Docker Compose para Stack RAG

Subir Qdrant + serviço Python de ingestão/runtime no Oracle Cloud.

## `docker-compose.yml`

```yaml
version: "3.9"

services:
  qdrant:
    image: qdrant/qdrant:v1.12.0
    container_name: sci_qdrant
    restart: unless-stopped
    ports:
      - "6333:6333"  # HTTP API
      - "6334:6334"  # gRPC (mais rápido para batch)
    volumes:
      - qdrant_data:/qdrant/storage
    environment:
      QDRANT__SERVICE__API_KEY: ${QDRANT_API_KEY}
      QDRANT__SERVICE__ENABLE_TLS: "false"  # use traefik/nginx para TLS
      QDRANT__STORAGE__OPTIMIZERS__INDEXING_THRESHOLD: 10000
      QDRANT__TELEMETRY_DISABLED: "true"
    networks:
      - sci_net
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:6333/healthz"]
      interval: 30s
      timeout: 10s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 4G
        reservations:
          memory: 2G

  rag_worker:
    build:
      context: ./rag_engine
      dockerfile: Dockerfile
    container_name: sci_rag_worker
    restart: unless-stopped
    depends_on:
      qdrant:
        condition: service_healthy
    environment:
      QDRANT_URL: http://qdrant:6333
      QDRANT_API_KEY: ${QDRANT_API_KEY}
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY}
      REDIS_URL: redis://redis:6379/2
      DJANGO_DB_URL: ${DJANGO_DB_URL}
      ORACLE_STORAGE_BUCKET: ${ORACLE_STORAGE_BUCKET}
    volumes:
      - ./prompts:/app/prompts:ro
      - bge_cache:/root/.cache/huggingface
    networks:
      - sci_net
    deploy:
      resources:
        limits:
          memory: 6G  # BGE-M3 em FP16 usa ~3GB

  # Opcional: MinIO se não quiser Oracle Object Storage
  minio:
    image: minio/minio:latest
    container_name: sci_minio
    restart: unless-stopped
    ports:
      - "9000:9000"
      - "9001:9001"
    volumes:
      - minio_data:/data
    environment:
      MINIO_ROOT_USER: ${MINIO_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_PASS}
    command: server /data --console-address ":9001"
    networks:
      - sci_net

volumes:
  qdrant_data:
  bge_cache:
  minio_data:

networks:
  sci_net:
    external: true  # mesma rede dos outros containers SCI
```

## `Dockerfile` do worker

```dockerfile
FROM python:3.11-slim

# Sistema
RUN apt-get update && apt-get install -y \
    tesseract-ocr tesseract-ocr-por \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependências Python pesadas primeiro (cache de layers)
COPY requirements-heavy.txt .
RUN pip install --no-cache-dir -r requirements-heavy.txt

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pré-baixar BGE-M3 para cache (evita download no primeiro request)
RUN python -c "from FlagEmbedding import BGEM3FlagModel; BGEM3FlagModel('BAAI/bge-m3', use_fp16=True)"

CMD ["python", "-m", "rag_engine.worker"]
```

## `requirements-heavy.txt`

```
torch==2.3.1
FlagEmbedding==1.3.4
docling==2.8.0
```

## `requirements.txt`

```
qdrant-client==1.12.0
anthropic==0.39.0
openai==1.55.0
pymupdf==1.24.13
paddleocr==2.9.0
pillow==10.4.0
pydantic==2.9.2
django==5.0  # se for usar como app Django
celery==5.4.0
redis==5.2.0
oci==2.140.0  # Oracle Cloud SDK, se usar Object Storage
boto3==1.35.0  # alternativa MinIO/S3
python-dotenv==1.0.1
```

## `.env`

```bash
# Qdrant
QDRANT_API_KEY=gere-uma-chave-forte-aqui-32-chars-min

# LLMs
ANTHROPIC_API_KEY=sk-ant-...
DEEPSEEK_API_KEY=sk-...

# Object Storage
ORACLE_STORAGE_BUCKET=sci-rag-images
MINIO_USER=minio_admin
MINIO_PASS=senha-forte-aqui

# SCI existente
DJANGO_DB_URL=mysql://user:pass@mariadb:3306/sci
```

## Subindo

```bash
# Na VM Oracle Cloud
cd /opt/sci
docker network create sci_net  # se não existir
docker compose -f docker-compose.rag.yml up -d

# Verificar
curl http://localhost:6333/healthz
curl http://localhost:6333/collections
```

## Integração com SCI existente

No `settings.py` do Django SCI:

```python
INSTALLED_APPS = [
    # ... apps existentes ...
    'rag_engine',
    'knowledge_ingestion',
]

QDRANT_CONFIG = {
    'url': os.getenv('QDRANT_URL', 'http://qdrant:6333'),
    'api_key': os.getenv('QDRANT_API_KEY'),
    'collection': 'sci_faq_ecd_ecf',
}
```

No `bot_engine` existente, no ponto onde o bot decide responder:

```python
from rag_engine.runtime import responder_mensagem, ContextoCliente

def processar_mensagem_cliente(mensagem, ticket):
    if ticket.bot_bloqueado_ciclo:
        return  # já está com humano

    ctx = ContextoCliente(
        nome=ticket.cliente.nome,
        empresa=ticket.cliente.empresa,
        licenca=ticket.cliente.licenca,
        tempo_cliente=ticket.cliente.tempo_relacionamento,
        historico_recente=ticket.ultimas_5_mensagens_serializadas(),
    )

    resposta = responder_mensagem(mensagem.texto, ctx)

    if resposta.acao == "TRANSFERIR_HUMANO":
        ticket.transferir_para_humano(departamento=resposta.departamento_sugerido or "suporte_contabil")
        evolution_api.enviar_texto(ticket.numero, resposta.mensagens[0])
        return

    # Envia mensagens em ordem com pequenos delays (parece humano)
    for i, msg in enumerate(resposta.mensagens):
        # Verifica se há imagem para enviar ANTES desta mensagem
        for img in resposta.imagens_a_enviar:
            if img['ordem_no_envio'] == i:
                evolution_api.enviar_imagem(ticket.numero, img['imagem_id_url'], legenda=img['legenda'])
                time.sleep(1.5)

        evolution_api.enviar_texto(ticket.numero, msg)
        time.sleep(random.uniform(1.0, 2.5))

    # Log para auditoria
    RAGQueryLog.objects.create(
        ticket=ticket,
        mensagem_cliente=mensagem.texto,
        resposta_json=asdict(resposta),
        faqs_consultados=resposta.faqs_consultados,
        confianca=resposta.confianca,
        modelo_usado=resposta.debug['modelo_usado'],
    )
```
