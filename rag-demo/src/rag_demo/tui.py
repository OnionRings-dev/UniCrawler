from __future__ import annotations

from langchain_core.messages import BaseMessage
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Input, Markdown, Static

from rag_demo.agent import ask
from rag_demo.cli import build_runtime


class RagDemoTui(App[None]):
    CSS = """
    Screen {
        layout: vertical;
    }

    #status {
        dock: top;
        height: 1;
        padding: 0 1;
        background: $surface;
        color: $text-muted;
    }

    #messages {
        height: 1fr;
        overflow-y: auto;
        padding: 1 2;
    }

    #composer {
        dock: bottom;
        height: 3;
        padding: 0 1;
        background: $panel;
    }

    #prompt {
        width: 5;
        content-align: center middle;
        color: $accent;
    }

    #input {
        width: 1fr;
    }

    .message {
        margin-bottom: 1;
    }

    .user {
        color: $accent;
    }

    .assistant {
        color: $success;
    }

    .error {
        color: $error;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Esci"),
        Binding("ctrl+l", "clear_messages", "Pulisci"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.settings = None
        self.retriever = None
        self.agent = None
        self.history: list[BaseMessage] = []
        self.transcript = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Avvio runtime...", id="status")
        yield Markdown("", id="messages")
        with Horizontal(id="composer"):
            yield Static("Tu >", id="prompt")
            yield Input(placeholder="Scrivi una domanda e premi Invio...", id="input")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "rag-demo"
        self.sub_title = "Agentic RAG over Qdrant"
        self.query_one("#input", Input).disabled = True
        self.initialize_runtime()

    @work(thread=True)
    def initialize_runtime(self) -> None:
        try:
            settings, retriever = build_runtime()
            from rag_demo.agent import build_agent

            agent = build_agent(settings, retriever)
        except Exception as exc:
            self.call_from_thread(self.show_error, f"Errore runtime: {exc}")
            return

        def ready() -> None:
            self.settings = settings
            self.retriever = retriever
            self.agent = agent
            self.query_one("#status", Static).update(
                f"Pronto | provider={settings.chat_provider} model={settings.chat_model} "
                f"| qdrant={settings.qdrant_url}"
            )
            input_box = self.query_one("#input", Input)
            input_box.disabled = False
            input_box.focus()
            self.append_message(
                "assistant",
                "Ciao. Fammi una domanda sui contenuti indicizzati in Qdrant.",
            )

        self.call_from_thread(ready)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        question = event.value.strip()
        if not question:
            return
        event.input.value = ""
        self.append_message("user", question)
        self.set_busy(True)
        self.ask_agent(question)

    @work(thread=True)
    def ask_agent(self, question: str) -> None:
        if self.agent is None or self.retriever is None:
            self.call_from_thread(self.show_error, "Runtime non ancora pronto.")
            self.call_from_thread(self.set_busy, False)
            return

        try:
            answer, history = ask(self.agent, question, self.retriever, self.history)
        except Exception as exc:
            self.call_from_thread(self.show_error, str(exc))
            self.call_from_thread(self.set_busy, False)
            return

        def done() -> None:
            self.history = history
            self.append_message("assistant", answer)
            self.set_busy(False)

        self.call_from_thread(done)

    def append_message(self, role: str, content: str) -> None:
        messages = self.query_one("#messages", Markdown)
        if role == "user":
            label = "Tu"
        elif role == "error":
            label = "Errore"
        else:
            label = "Agente"
        block = f"\n\n**{label}:**\n\n{content}"
        self.transcript = (self.transcript + block).strip()
        messages.update(self.transcript)
        messages.scroll_end(animate=False)
        self.query_one("#input", Input).focus()

    def set_busy(self, busy: bool) -> None:
        input_box = self.query_one("#input", Input)
        input_box.disabled = busy
        self.query_one("#status", Static).update(
            "Sto interrogando Qdrant e l'agente..." if busy else self.ready_status()
        )
        if not busy:
            input_box.focus()

    def ready_status(self) -> str:
        if self.settings is None:
            return "Avvio runtime..."
        return (
            f"Pronto | provider={self.settings.chat_provider} "
            f"model={self.settings.chat_model} | qdrant={self.settings.qdrant_url}"
        )

    def show_error(self, message: str) -> None:
        self.append_message("error", f"Errore: {message}")
        self.query_one("#status", Static).update(f"Errore: {message}")

    def action_clear_messages(self) -> None:
        self.history = []
        self.transcript = ""
        self.query_one("#messages", Markdown).update("")
        self.append_message("assistant", "Conversazione pulita.")


def run_tui() -> None:
    RagDemoTui().run()
