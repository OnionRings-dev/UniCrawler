from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from rag_demo.config import Settings
from rag_demo.reranker import Reranker, hit_text, make_reranker
from rag_demo.retriever import QdrantRetriever, RetrievalHit, dumps_for_tool


SYSTEM_PROMPT = """Sei un assistente RAG per valutare i contenuti indicizzati in Qdrant da UniCrawler.

Usa i tool disponibili in modo autonomo:
- prima scopri le collection quando non sai dove cercare;
- cerca in Qdrant prima di rispondere a domande sui contenuti;
- fai piu' ricerche con formulazioni diverse se la prima non basta;
- se il database non contiene prove sufficienti, dillo chiaramente.

Non spiegare mai all'utente quali tool dovrebbe usare. I tool sono tuoi strumenti interni: usali tu e rispondi solo con il risultato finale.
Non aggiungere sezioni Fonti, Sorgenti, Citazioni o Bibliografia: le fonti vengono aggiunte automaticamente dal programma.
Rispondi in italiano, in modo sintetico ma utile. Non inventare fonti o dettagli non presenti nei chunk.
"""


SOURCE_SECTION_RE = re.compile(
    r"(?ims)\n{0,2}(?:\*\*)?(?:fonti|sorgenti|citazioni|bibliografia)(?:\*\*)?\s*:.*$"
)


@dataclass(frozen=True)
class AgentAnswer:
    answer: str
    messages: list[BaseMessage]
    sources: list["Source"]
    tool_calls: list["ToolCallSummary"]


@dataclass(frozen=True)
class Source:
    title: str
    url: str
    collection: str
    score: float
    chunk_id: object


@dataclass(frozen=True)
class ToolCallSummary:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class RankedHit:
    hit: RetrievalHit
    keyword_score: float
    reranker_score: float

    @property
    def final_score(self) -> float:
        return self.hit.score + self.keyword_score + self.reranker_score


def build_tools(retriever: QdrantRetriever):
    @tool
    def list_qdrant_collections() -> str:
        """Lista le collection Qdrant disponibili per questa demo, con conteggi indicativi."""
        return dumps_for_tool(retriever.collection_summaries())

    @tool
    def search_qdrant(
        query: str,
        collection: str | None = None,
        domain: str | None = None,
        document_type: str | None = None,
        limit: int | None = None,
    ) -> str:
        """Cerca chunk semanticamente rilevanti in Qdrant. Usa collection, domain o document_type solo se noti."""
        hits = retriever.search(
            query=query,
            collection=collection,
            domain=domain,
            document_type=document_type,
            limit=limit,
        )
        return dumps_for_tool([hit.to_agent_dict() for hit in hits])

    @tool
    def get_qdrant_payload(collection: str, point_id: str) -> str:
        """Recupera il payload completo di un punto Qdrant gia' trovato con search_qdrant."""
        payload = retriever.get_payload(collection=collection, point_id=point_id)
        return dumps_for_tool(payload or {"error": "point not found"})

    return [list_qdrant_collections, search_qdrant, get_qdrant_payload]


def build_agent(settings: Settings, retriever: QdrantRetriever):
    if settings.chat_provider == "groq":
        if not settings.groq_api_key:
            raise ValueError("GROQ_API_KEY is required when RAG_CHAT_PROVIDER=groq.")
        llm = ChatGroq(
            model=settings.chat_model,
            temperature=0,
            api_key=settings.groq_api_key,
        )
        return create_react_agent(llm, build_tools(retriever), prompt=SYSTEM_PROMPT)

    if settings.chat_provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when RAG_CHAT_PROVIDER=openai.")
        llm = ChatOpenAI(
            model=settings.chat_model,
            temperature=0,
            api_key=settings.openai_api_key,
        )
        return create_react_agent(llm, build_tools(retriever), prompt=SYSTEM_PROMPT)

    raise ValueError(f"Unsupported RAG_CHAT_PROVIDER: {settings.chat_provider}")


def last_ai_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return str(message.content)
    return ""


def format_retrieval_context(hits: list[RetrievalHit]) -> str:
    if not hits:
        return "Nessun chunk rilevante trovato in Qdrant per questa domanda."

    blocks: list[str] = []
    for index, hit in enumerate(hits, start=1):
        payload = hit.payload
        title = payload.get("title") or "Senza titolo"
        url = payload.get("final_url") or payload.get("url") or "URL non disponibile"
        headings = " > ".join(payload.get("headings") or [])
        text = payload.get("text") or ""
        blocks.append(
            "\n".join(
                [
                    f"[{index}] collection={hit.collection} score={hit.score:.4f}",
                    f"Titolo: {title}",
                    f"URL: {url}",
                    f"Tipo: {payload.get('document_type')} chunk={payload.get('chunk_id')}",
                    f"Headings: {headings}" if headings else "Headings: n/a",
                    f"Testo:\n{text}",
                ]
            )
        )
    return "\n\n---\n\n".join(blocks)


def expanded_queries(question: str) -> list[str]:
    queries = [question]
    normalized = question.lower()

    if any(term in normalized for term in ("iscriv", "iscriz", "iscriver")):
        queries.extend(
            [
                "Iscrizioni online",
                "iscrizioni online studenti scuola portale ministeriale",
                "iscrizioni scuola servizio online autenticazione segreteria",
            ]
        )

    queries.append(f"{question} procedura requisiti scadenze contatti")

    deduped: list[str] = []
    for query in queries:
        if query not in deduped:
            deduped.append(query)
    return deduped


def retrieve_for_question(question: str, retriever: QdrantRetriever) -> list[RetrievalHit]:
    hits, _ = retrieve_for_question_with_trace(question, retriever)
    return hits


def retrieve_for_question_with_trace(
    question: str,
    retriever: QdrantRetriever,
) -> tuple[list[RetrievalHit], list[ToolCallSummary]]:
    settings = retriever.settings
    hits_by_source: dict[tuple[str, str], RetrievalHit] = {}
    tool_calls: list[ToolCallSummary] = []

    for query in expanded_queries(question):
        tool_calls.append(
            ToolCallSummary(
                name="search_qdrant",
                args={
                    "query": query,
                    "limit": settings.candidate_limit,
                    "phase": "pre_retrieval",
                },
            )
        )
        merge_deduped_hits(
            hits_by_source,
            retriever.search(query, limit=settings.candidate_limit),
        )

    candidates = list(hits_by_source.values())
    reranker = make_reranker(settings)
    ranked_hits = rank_hits(
        question,
        candidates,
        reranker=reranker,
        keyword_boost=settings.keyword_boost,
    )
    tool_calls.append(
        ToolCallSummary(
            name="rerank",
            args={
                "candidates": len(candidates),
                "provider": reranker.provider,
                "model": reranker.model,
            },
        )
    )
    return [ranked_hit_for_context(ranked) for ranked in ranked_hits[: settings.context_limit]], tool_calls


def ranked_hit_for_context(ranked: RankedHit) -> RetrievalHit:
    payload = dict(ranked.hit.payload)
    payload["_vector_score"] = ranked.hit.score
    payload["_keyword_score"] = ranked.keyword_score
    payload["_reranker_score"] = ranked.reranker_score
    return RetrievalHit(
        collection=ranked.hit.collection,
        point_id=ranked.hit.point_id,
        score=ranked.final_score,
        payload=payload,
    )


def merge_deduped_hits(
    hits_by_source: dict[tuple[str, str], RetrievalHit],
    hits: list[RetrievalHit],
) -> None:
    for hit in hits:
        payload = hit.payload
        source = str(payload.get("final_url") or payload.get("url") or hit.point_id)
        key = (hit.collection, source)
        current = hits_by_source.get(key)
        if current is None or hit.score > current.score:
            hits_by_source[key] = hit


def rank_hits(
    question: str,
    hits: list[RetrievalHit],
    reranker: Reranker,
    keyword_boost: bool,
) -> list[RankedHit]:
    reranker_scores = normalize_scores(reranker.score(question, hits))
    ranked = [
        RankedHit(
            hit=hit,
            keyword_score=keyword_score(question, hit) if keyword_boost else 0.0,
            reranker_score=reranker_scores[index] if index < len(reranker_scores) else 0.0,
        )
        for index, hit in enumerate(hits)
    ]
    return sorted(ranked, key=lambda ranked_hit: ranked_hit.final_score, reverse=True)


def normalize_scores(scores: list[float]) -> list[float]:
    if not scores:
        return []
    low = min(scores)
    high = max(scores)
    if high == low:
        return [0.0 for _ in scores]
    return [(score - low) / (high - low) for score in scores]


def keyword_score(question: str, hit: RetrievalHit) -> float:
    payload = hit.payload
    title = str(payload.get("title") or "")
    headings = " ".join(payload.get("headings") or [])
    text = str(payload.get("text") or "")
    query_terms = tokenize(question)

    score = lexical_field_score(query_terms, title, weight=0.06, cap=0.36)
    score += lexical_field_score(query_terms, headings, weight=0.04, cap=0.24)
    score += lexical_field_score(query_terms, text, weight=0.015, cap=0.18)
    score += phrase_score(question, title, headings, text)
    return score


def tokenize(value: str) -> set[str]:
    return {token for token in re.findall(r"\w+", value.lower()) if len(token) >= 3}


def lexical_field_score(query_terms: set[str], value: str, weight: float, cap: float) -> float:
    if not query_terms or not value:
        return 0.0
    field_terms = tokenize(value)
    matches = len(query_terms & field_terms)
    return min(matches * weight, cap)


def phrase_score(question: str, title: str, headings: str, text: str) -> float:
    normalized_question = question.lower()
    score = 0.0
    phrase_candidates = [
        "iscrizioni online",
        "portale ministeriale",
        "spid",
        "cie",
        "eidas",
    ]

    for phrase in phrase_candidates:
        if phrase not in normalized_question and phrase not in f"{title} {headings} {text}".lower():
            continue
        if phrase in title.lower():
            score += 0.22
        if phrase in headings.lower():
            score += 0.14
        if phrase in text.lower():
            score += 0.06

    return score


def rerank_score(question: str, hit: RetrievalHit) -> float:
    """Backward-compatible heuristic score used by older callers/tests."""
    payload = hit.payload
    title = str(payload.get("title") or "").lower()
    headings = " ".join(payload.get("headings") or []).lower()
    text = str(payload.get("text") or "").lower()
    question_lower = question.lower()
    score = hit.score

    if any(term in question_lower for term in ("iscriv", "iscriz", "iscriver")):
        if "iscrizioni" in title or "iscrizione" in title:
            score += 0.2
        if "iscrizioni online" in headings:
            score += 0.12
        if "spid" in text and "cie" in text:
            score += 0.08
        if "portale ministeriale" in text:
            score += 0.05

    return score + keyword_score(question, hit)


def build_grounded_question(question: str, hits: list[RetrievalHit]) -> str:
    return f"""Domanda utente:
{question}

Contesto recuperato automaticamente da Qdrant:
{format_retrieval_context(hits)}

Istruzioni per la risposta:
- Rispondi alla domanda usando solo il contesto qui sopra e, se serve, i tool disponibili per cercare meglio.
- Non dire all'utente di usare i tool.
- Se il contesto non contiene una procedura chiara, spiega cosa risulta e cosa manca.
- Non aggiungere fonti, citazioni, URL finali, bibliografia o sezioni simili: verranno aggiunte automaticamente dal programma.
"""


def sources_from_hits(hits: list[RetrievalHit], limit: int) -> list[Source]:
    sources: list[Source] = []
    seen_urls: set[str] = set()

    for hit in hits:
        payload = hit.payload
        url = str(payload.get("final_url") or payload.get("url") or "").strip()
        if not url or url in seen_urls:
            continue

        seen_urls.add(url)
        sources.append(
            Source(
                title=str(payload.get("title") or "Senza titolo"),
                url=url,
                collection=hit.collection,
                score=hit.score,
                chunk_id=payload.get("chunk_id"),
            )
        )

    return sources[:limit]


def strip_model_sources(answer: str) -> str:
    return SOURCE_SECTION_RE.sub("", answer).strip()


def format_sources(sources: list[Source]) -> str:
    if not sources:
        return "\n\n**Fonti**\n\nNessuna fonte recuperata da Qdrant."

    lines = ["", "", "**Fonti**"]
    for index, source in enumerate(sources, start=1):
        detail = (
            f"collection: `{source.collection}`, chunk: `{source.chunk_id}`, "
            f"score: `{source.score:.4f}`"
        )
        lines.append(f"{index}. {source.title}  \n   URL: {source.url}  \n   {detail}")
    return "\n".join(lines)


def tool_calls_from_messages(messages: list[BaseMessage]) -> list[ToolCallSummary]:
    calls: list[ToolCallSummary] = []

    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for tool_call in message.tool_calls:
            calls.append(
                ToolCallSummary(
                    name=str(tool_call.get("name") or "unknown"),
                    args=dict(tool_call.get("args") or {}),
                )
            )

    return calls


def format_tool_calls(tool_calls: list[ToolCallSummary]) -> str:
    if not tool_calls:
        return "\n\n**Tool chiamati**\n\nNessun tool chiamato dall'agente."

    lines = ["", "", "**Tool chiamati**"]
    for index, tool_call in enumerate(tool_calls, start=1):
        query = tool_call.args.get("query")
        collection = tool_call.args.get("collection")
        candidates = tool_call.args.get("candidates")
        provider = tool_call.args.get("provider")
        model = tool_call.args.get("model")
        parts: list[str] = []
        if query:
            parts.append(f"query={short_arg(str(query))!r}")
        if collection:
            parts.append(f"collection={collection!r}")
        if candidates is not None:
            parts.append(f"candidates={candidates!r}")
        if provider:
            parts.append(f"provider={provider!r}")
        if model:
            parts.append(f"model={short_arg(str(model), max_length=48)!r}")
        args = ", ".join(parts)
        lines.append(f"{index}. `{tool_call.name}`" + (f" ({args})" if args else ""))
    return "\n".join(lines)


def short_arg(value: str, max_length: int = 72) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 1]}..."


def append_metadata(answer: str, tool_calls: list[ToolCallSummary], sources: list[Source]) -> str:
    clean_answer = strip_model_sources(answer)
    return f"{clean_answer}{format_tool_calls(tool_calls)}{format_sources(sources)}"


def ask_with_sources(
    agent,
    question: str,
    retriever: QdrantRetriever,
    history: list[BaseMessage] | None = None,
) -> AgentAnswer:
    hits, retrieval_tool_calls = retrieve_for_question_with_trace(question, retriever)
    messages: list[BaseMessage] = []
    if history:
        messages.extend(history)
    messages.append(HumanMessage(content=build_grounded_question(question, hits)))
    result = agent.invoke({"messages": messages})
    output_messages = list(result["messages"])
    sources = sources_from_hits(hits, limit=retriever.settings.source_limit)
    tool_calls = retrieval_tool_calls + tool_calls_from_messages(output_messages)
    answer = append_metadata(last_ai_text(output_messages), tool_calls, sources)
    return AgentAnswer(
        answer=answer,
        messages=output_messages,
        sources=sources,
        tool_calls=tool_calls,
    )


def ask(
    agent,
    question: str,
    retriever: QdrantRetriever,
    history: list[BaseMessage] | None = None,
) -> tuple[str, list[BaseMessage]]:
    result = ask_with_sources(agent, question, retriever, history)
    return result.answer, result.messages
