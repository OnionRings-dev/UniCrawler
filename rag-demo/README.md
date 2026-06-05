# rag-demo

Demo CLI per valutare i dati indicizzati in Qdrant da UniCrawler con un RAG agentico basato su LangChain e LangGraph.

L'agent ha tre tool:

- `list_qdrant_collections`: scopre le collection disponibili.
- `search_qdrant`: cerca chunk semanticamente rilevanti in Qdrant.
- `get_qdrant_payload`: recupera il payload completo di un punto gia' trovato.

## Setup

```sh
cd rag-demo
uv sync
```

La demo carica prima il file `.env` della root del repo e poi `rag-demo/.env`, se presente. Se eredita `QDRANT_URL=http://qdrant:6333` dal compose, lo converte automaticamente in `http://localhost:6333` per l'esecuzione da host.

```sh
cp .env.example .env
```

Configura almeno:

```dotenv
GROQ_API_KEY=...
RAG_QDRANT_URL=http://localhost:6333
```

Se vuoi limitare la demo a collection specifiche:

```dotenv
RAG_COLLECTIONS=unicrawler_example_com,unicrawler_docs_example_com
```

## Comandi

Mostra le collection disponibili:

```sh
uv run rag-demo collections
```

Esegue una ricerca vettoriale diretta, utile per debug senza LLM:

```sh
uv run rag-demo search "Quali corsi sono disponibili?"
```

Fa una domanda one-shot all'agent:

```sh
uv run rag-demo ask "Quali informazioni ci sono sui bandi?"
```

Avvia la chat interattiva:

```sh
uv run rag-demo
```

Avvia la TUI:

```sh
uv run rag-demo tui
```

Nella TUI premi `Invio` per inviare un messaggio, `Ctrl+L` per pulire la conversazione e `Ctrl+C` per uscire.

## Variabili utili

- `RAG_QDRANT_URL`, fallback a `QDRANT_URL`, default `http://localhost:6333`.
- `RAG_QDRANT_API_KEY`, fallback a `QDRANT_API_KEY`.
- `RAG_COLLECTION_PREFIX`, fallback a `VECTORIZER_COLLECTION_PREFIX`, default `unicrawler`.
- `RAG_COLLECTIONS`, lista separata da virgole; se vuota usa tutte le collection con prefix.
- `RAG_EMBEDDING_PROVIDER`, fallback a `VECTORIZER_EMBEDDING_PROVIDER`, default `fastembed`.
- `RAG_EMBEDDING_MODEL`, fallback a `VECTORIZER_EMBEDDING_MODEL`, default `BAAI/bge-small-en-v1.5`.
- `RAG_CHAT_PROVIDER`, `groq` oppure `openai`, default `groq`.
- `RAG_CHAT_MODEL`, default `llama-3.3-70b-versatile` per Groq.
- `RAG_CANDIDATE_LIMIT`, candidati Qdrant per query espansa, default `30`.
- `RAG_CONTEXT_LIMIT`, chunk finali passati al modello dopo reranking, default `5`.
- `RAG_SOURCE_LIMIT`, fonti mostrate in output, default `3`.
- `RAG_RERANKER_PROVIDER`, `local` oppure `off`, default `local`.
- `RAG_RERANKER_MODEL`, default `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`.
- `RAG_KEYWORD_BOOST`, abilita boost lessicale su title/headings/text, default `true`.
- `RAG_MAX_TOOL_RESULTS`, default `12`.

Usa lo stesso provider e modello di embedding del vectorizer, altrimenti le query non saranno nello stesso spazio vettoriale dei dati indicizzati.

Il reranker locale scarica il modello Hugging Face al primo utilizzo. Per una modalità più rapida ma meno precisa:

```dotenv
RAG_RERANKER_PROVIDER=off
```

Per usare OpenAI come modello chat invece di Groq:

```dotenv
RAG_CHAT_PROVIDER=openai
RAG_CHAT_MODEL=gpt-4.1-mini
OPENAI_API_KEY=...
```
