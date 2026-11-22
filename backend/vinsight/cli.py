"""Text interface. `vinsight "Is 1HGCM82633A004352 worth buying?"` or just `vinsight` for a chat."""

import argparse
import asyncio
import json

from rich.console import Console
from rich.markdown import Markdown

from vinsight.agent import DEFAULT_PERSONA, PERSONAS, Agent, ToolBox, TraceEvent
from vinsight.llm import PROVIDERS

console = Console()


def _preview(value, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value)
    return text if len(text) <= limit else text[:limit] + "…"


class Renderer:
    """Prints a streamed turn: thinking dims inline as it arrives; answer text is buffered and
    rendered as Markdown once the step's text is complete."""

    def __init__(self, show_trace: bool):
        self.show_trace = show_trace
        self.text: list[str] = []
        self.thinking = False

    def _flush(self) -> None:
        if self.thinking:
            console.print()
            self.thinking = False
        if self.text:
            console.print(Markdown("".join(self.text)))
            self.text = []

    def __call__(self, event: TraceEvent) -> None:
        if event.type == "text":
            self.text.append(event.text or "")
            return
        if event.type == "thinking":
            if self.show_trace:
                if not self.thinking:
                    console.print("[dim italic]· thinking: [/]", end="")
                    self.thinking = True
                console.print(f"[dim italic]{event.text}[/]", end="")
            return
        self._flush()
        if event.type == "error":
            console.print(f"[bold red]error:[/] {event.text}")
        elif event.type == "notice":
            console.print(f"[yellow]↪ {event.text}[/]")
        elif not self.show_trace:
            return
        elif event.type == "tool_call":
            console.print(f"[cyan]→ {event.name}[/]([dim]{_preview(event.input)}[/])")
        elif event.type == "tool_result":
            mark = "[green]✓[/]" if event.ok else "[red]✗[/]"
            console.print(f"  {mark} {event.name} [dim]{event.ms:.0f} ms · {_preview(event.output)}[/]")
        elif event.type == "done":
            u = event.usage or {}
            console.print(
                f"[dim]done in {event.ms / 1000:.1f}s · {event.model} · {u.get('input_tokens', 0)} in "
                f"/ {u.get('output_tokens', 0)} out · {u.get('cache_read_input_tokens', 0)} cached[/]"
            )


async def _main(prompt: str | None, persona: str, provider: str | None, show_trace: bool) -> None:
    async with ToolBox() as tools:
        agent = Agent(tools)
        if prompt:
            render = Renderer(show_trace)
            async for event in agent.run(prompt, persona, provider):
                render(event)
            return
        console.print(f"[bold]VINsight[/] · {PERSONAS[persona].name} — {PERSONAS[persona].tagline} Ctrl-D to quit.")
        while True:
            try:
                line = console.input("[bold green]you>[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                return
            if line:
                render = Renderer(show_trace)
                async for event in agent.run(line, persona, provider):
                    render(event)


def main() -> None:
    parser = argparse.ArgumentParser(prog="vinsight", description=__doc__)
    parser.add_argument("prompt", nargs="*", help="one-shot question; omit for interactive chat")
    parser.add_argument("-p", "--persona", choices=list(PERSONAS), default=DEFAULT_PERSONA)
    parser.add_argument("-m", "--model", choices=list(PROVIDERS), help="provider for this run (default from .env)")
    parser.add_argument("--no-trace", action="store_true", help="hide the reasoning and tool trace")
    args = parser.parse_args()
    asyncio.run(_main(" ".join(args.prompt) or None, args.persona, args.model, not args.no_trace))


if __name__ == "__main__":
    main()
