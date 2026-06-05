from __future__ import annotations

import argparse
import sys

from langchain_core.messages import BaseMessage
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from rag_demo.agent import ask, build_agent
from rag_demo.config import load_settings
from rag_demo.embeddings import make_embedder
from rag_demo.retriever import QdrantRetriever, dumps_for_tool


console = Console()


def build_runtime():
    settings = load_settings()
    embedder = make_embedder(
        settings.embedding_provider,
        settings.embedding_model,
        settings.openai_api_key,
    )
    retriever = QdrantRetriever(settings, embedder)
    return settings, retriever


def cmd_collections() -> int:
    _, retriever = build_runtime()
    console.print(dumps_for_tool(retriever.collection_summaries()))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    _, retriever = build_runtime()
    hits = retriever.search(
        query=args.query,
        collection=args.collection,
        domain=args.domain,
        document_type=args.document_type,
        limit=args.limit,
    )
    console.print(dumps_for_tool([hit.to_agent_dict() for hit in hits]))
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    settings, retriever = build_runtime()
    agent = build_agent(settings, retriever)
    answer, _ = ask(agent, args.question, retriever)
    console.print(Markdown(answer))
    return 0


def cmd_chat() -> int:
    settings, retriever = build_runtime()
    agent = build_agent(settings, retriever)
    history: list[BaseMessage] = []

    console.print(
        Panel.fit(
            "RAG demo pronta. Scrivi una domanda, oppure `exit` per uscire.",
            title="rag-demo",
        )
    )
    while True:
        try:
            question = console.input("[bold cyan]Tu[/bold cyan] > ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return 0

        if question.lower() in {"exit", "quit", ":q"}:
            return 0
        if not question:
            continue

        try:
            answer, history = ask(agent, question, retriever, history)
        except Exception as exc:
            console.print(f"[red]Errore:[/red] {exc}")
            continue
        console.print("[bold green]Bot[/bold green]")
        console.print(Markdown(answer))


def cmd_tui() -> int:
    from rag_demo.tui import run_tui

    run_tui()
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="rag-demo",
        description="Demo agentica LangGraph/LangChain per interrogare le collection Qdrant di UniCrawler.",
    )
    subcommands = root.add_subparsers(dest="command")

    subcommands.add_parser("collections", help="Mostra le collection Qdrant usate dalla demo.")

    search = subcommands.add_parser("search", help="Esegue una ricerca vettoriale diretta senza LLM.")
    search.add_argument("query")
    search.add_argument("--collection")
    search.add_argument("--domain")
    search.add_argument("--document-type")
    search.add_argument("--limit", type=int)

    ask_parser = subcommands.add_parser("ask", help="Fa una domanda one-shot all'agent RAG.")
    ask_parser.add_argument("question")

    subcommands.add_parser("chat", help="Avvia una chat interattiva.")
    subcommands.add_parser("tui", help="Avvia una TUI per conversare con l'agent.")
    return root


def main() -> None:
    args = parser().parse_args()
    command = args.command or "chat"

    try:
        if command == "collections":
            raise SystemExit(cmd_collections())
        if command == "search":
            raise SystemExit(cmd_search(args))
        if command == "ask":
            raise SystemExit(cmd_ask(args))
        if command == "chat":
            raise SystemExit(cmd_chat())
        if command == "tui":
            raise SystemExit(cmd_tui())
    except ValueError as exc:
        console.print(f"[red]Configurazione non valida:[/red] {exc}")
        raise SystemExit(2) from exc
    except Exception as exc:
        console.print(f"[red]Errore:[/red] {exc}")
        raise SystemExit(1) from exc

    console.print(f"[red]Comando sconosciuto:[/red] {command}", file=sys.stderr)
    raise SystemExit(2)
