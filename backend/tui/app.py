import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from rich.markdown import Markdown
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Select, Static

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

try:
    from backend.version import __version__
except Exception:
    __version__ = "2.4.0"


def _llm(method, path, body=None, timeout=30):
    url = f"{OLLAMA_HOST}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except urllib.error.URLError as e:
        return {"error": f"Cannot connect to Ollama: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


class HelpScreen(ModalScreen):
    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("[bold #4ADE80]LAC[/bold #4ADE80] [dim]— Commands[/dim]")
            yield Static(
                "  [bold]/clear[/]        Clear conversation\n"
                "  [bold]/model[/] [n]    Switch model\n"
                "  [bold]/system[/] [p]   Set system prompt\n"
                "  [bold]/save[/] [n]     Save session\n"
                "  [bold]/load[/] [n]     Load session\n"
                "  [bold]/list[/]         List sessions\n"
                "  [bold]/help[/]         Show this help\n"
                "  [bold]/agent[/] [n]    List or switch agent\n"
                "  [bold]/theme[/] [n]    List or switch theme\n"
                "  [bold]/exit[/]         Quit\n"
                "\n"
                "  [dim]Ctrl+P  Model     Ctrl+S  Save[/dim]\n"
                "  [dim]Ctrl+L  Load      Ctrl+H  Help[/dim]\n"
                "  [dim]Ctrl+Q  Quit      Esc     Close[/dim]"
            )
            yield Static("[dim]Press any key to close[/dim]", id="hint")

    def on_key(self) -> None:
        self.dismiss(None)


class ModelScreen(ModalScreen):
    def __init__(self, models: list[str]):
        super().__init__()
        self.models = models or ["(none)"]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("[bold cyan]Select Model[/bold cyan]")
            yield Select(
                ((m, m) for m in self.models),
                prompt="Choose a model...",
                id="sel",
            )
            yield Static("[dim]Esc to cancel[/dim]", id="hint")

    @on(Select.Changed, "#sel")
    def on_sel(self, event: Select.Changed) -> None:
        if event.value:
            self.dismiss(event.value)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


class AptApp(App):
    TITLE = "LAC"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { background: $background; color: $foreground; }
    #bar { height: 1; padding: 0 2; background: $surface; color: $text-muted; dock: top; border-bottom: solid $boost; }
    #scroll { height: 1fr; margin: 0 2; background: $background; overflow-y: auto; padding-top: 1; }
    #scroll > Static { margin-bottom: 0; }
    #inp { dock: bottom; height: 3; margin: 1 2; background: $panel; border: tall $boost; color: $foreground; padding: 0 1; }
    #inp:focus { border: tall $primary; }
    #dialog { width: 56; height: auto; padding: 1 2; background: $surface; border: solid $primary; }
    #hint { margin-top: 1; color: $text-muted; }
    Select { background: $panel; border: tall $boost; color: $foreground; width: 1fr; }
    Select:focus { border: tall $primary; }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+p", "pick_model", "Model", show=True),
        Binding("ctrl+l", "load_session", "Load", show=True),
        Binding("ctrl+s", "save_session", "Save", show=True),
        Binding("ctrl+h", "show_help", "Help", show=True),
        Binding("ctrl+t", "cycle_theme", "Theme", show=True),
    ]

    def __init__(self):
        super().__init__()
        self.model = ""
        self.models: list[str] = []
        self.model_count = 0
        self.messages: list[dict] = []
        self.sid = None
        self.streaming = False
        self.system = ""
        self._stream_widget: Static | None = None
        self.agent = None
        self.agent_name = "build"
        self.agent_histories: dict[str, list] = {}
        self._mcp_manager = None
        self._mcp_ready = False
        self._last_update_time = 0.0
        self._perm_engine = None

    def compose(self) -> ComposeResult:
        yield Static("", id="bar")
        yield VerticalScroll(id="scroll")
        yield Input(placeholder="Type a message...  (/help for commands)", id="inp")

    def on_mount(self) -> None:
        self.title = "LAC"
        self.sub_title = f"v{__version__}"
        self._register_themes()
        self._bar("[dim]connecting to Ollama...[/dim]")
        self._refresh_models()
        self._start_mcp()

    def _register_themes(self) -> None:
        from .themes.apt_dark import apt_dark
        from .themes.apt_light import apt_light
        from .themes.apt_high_contrast import apt_high_contrast

        self._themes = ["apt-dark", "apt-light", "apt-high-contrast"]
        for theme in (apt_dark, apt_light, apt_high_contrast):
            try:
                self.register_theme(theme)
            except Exception:
                pass
        try:
            from ..config import resolve_config

            wanted = resolve_config().theme or "apt-dark"
        except Exception:
            wanted = "apt-dark"
        self._set_theme(wanted)

    def _set_theme(self, name: str) -> None:
        if not getattr(self, "_themes", None) or name not in self._themes:
            return
        try:
            self.theme = name
            self._theme_name = name
        except Exception:
            pass

    async def action_cycle_theme(self) -> None:
        themes = getattr(self, "_themes", None) or ["apt-dark"]
        cur = getattr(self, "_theme_name", "apt-dark")
        try:
            nxt = themes[(themes.index(cur) + 1) % len(themes)]
        except ValueError:
            nxt = themes[0]
        self._set_theme(nxt)
        await self._line(f"theme → [bold]{nxt}[/bold]")

    @work(thread=True)
    def _refresh_models(self) -> None:
        result = _llm("GET", "/api/tags")
        if "error" in result:
            self.call_from_thread(self._apply_models, [], 0, result["error"])
            return
        models = [m["name"] for m in result.get("models", [])]
        self.call_from_thread(self._apply_models, models, len(models), None)

    def _apply_models(self, models: list[str], count: int, error: str | None) -> None:
        self.models = models
        self.model_count = count
        if error:
            self._bar(f"[red]Ollama: {error}[/red]")
            return
        if models and not self.model:
            self.model = models[0]
            self._ensure_session()
        if not models:
            self._bar("[yellow]No models installed. Run: lac pull <model>[/yellow]")
            return
        self._bar()

    def _ensure_session(self) -> None:
        if self.sid:
            return
        try:
            from backend.cookbook.config import ensure_workspace
            from backend.cookbook.persistence import create_session

            ensure_workspace()
            self.sid = create_session(model=self.model)
        except Exception:
            pass

    def _bar(self, text: str | None = None) -> None:
        if text:
            self.query_one("#bar", Static).update(text)
            return
        self.query_one("#bar", Static).update(
            f"[bold #4ADE80]LAC[/bold #4ADE80] [dim]v{__version__}[/dim]  |  "
            f"[bold]{self.model or 'no model'}[/bold]  |  "
            f"[dim]{self.model_count} model{'s' if self.model_count != 1 else ''}[/dim]  |  "
            f"[magenta]{self.agent_name}[/magenta]"
        )

    def _scr(self) -> VerticalScroll:
        return self.query_one("#scroll", VerticalScroll)

    async def _msg(self, role: str, content: str | None = None) -> Static | None:
        s = self._scr()
        if role == "user":
            await s.mount(Static(Text("You", style="bold cyan")))
            w = Static(Text(str(content), style="white"))
            await s.mount(w)
            s.scroll_end()
            return w
        if role == "assistant":
            await s.mount(Static(Text(self.model, style="bold green")))
            w = Static("")
            await s.mount(w)
            if content:
                w.update(Markdown(content.strip(), code_theme="monokai"))
            s.scroll_end()
            return w
        if role == "sys":
            w = Static(Text(str(content), style="dim italic"))
            await s.mount(w)
            s.scroll_end()
            return w
        s.scroll_end()
        return None

    async def _line(self, content: str, style: str = "dim italic") -> Static:
        s = self._scr()
        w = Static(Text(str(content), style=style))
        await s.mount(w)
        s.scroll_end()
        return w

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_pick_model(self) -> None:
        if not self.models:
            self._line("[yellow]No models yet — refreshing...[/yellow]")
            self._refresh_models()
            return

        def _picked(m: str | None) -> None:
            if m:
                self.model = m
                self._bar()

        self.push_screen(ModelScreen(self.models), _picked)

    async def action_save_session(self) -> None:
        if not self.sid or not self.messages:
            await self._line("[yellow]nothing to save[/yellow]")
            return
        try:
            from backend.cookbook.persistence import save_session

            save_session(self.sid, model=self.model, messages=self.messages)
            await self._line(f"saved session {self.sid[:8]}")
        except Exception as e:
            await self._line(f"[red]save failed: {e}[/red]")

    async def action_load_session(self) -> None:
        try:
            from backend.cookbook.persistence import get_session
        except Exception:
            await self._line("[red]persistence unavailable[/red]")
            return
        data = get_session(self.sid) if self.sid else None
        if not data or not data.get("messages"):
            await self._line("[yellow]no saved session to load[/yellow]")
            return
        self.messages = data["messages"]
        if data.get("model"):
            self.model = data["model"]
        s = self._scr()
        await s.remove_children()
        for m in self.messages:
            await self._msg(m["role"], m.get("content", ""))
        await self._line(f"loaded {len(self.messages)} messages")
        self._bar()

    @work(thread=True, exclusive=True)
    def _run_stream(self, text: str) -> None:
        if not self.model:
            return
        payload = {"model": self.model, "messages": list(self.messages), "stream": True}
        if self.system:
            payload["messages"].insert(0, {"role": "system", "content": self.system})
        url = f"{OLLAMA_HOST}/api/chat"
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        full = ""
        try:
            resp = urllib.request.urlopen(req, timeout=300)
            for raw in resp:
                line = raw.decode().strip()
                if not line:
                    continue
                if line.startswith("data: "):
                    line = line[6:]
                if line == "[DONE]":
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("done"):
                    break
                c = obj.get("message", {}).get("content", "")
                if c:
                    full += c
                    self.call_from_thread(self._stream_update, full)
        except urllib.error.HTTPError as e:
            self.call_from_thread(self._stream_error, f"HTTP {e.code}: {e.read().decode()[:200]}")
            return
        except urllib.error.URLError as e:
            self.call_from_thread(self._stream_error, f"Connection: {e.reason}")
            return
        except Exception as e:
            self.call_from_thread(self._stream_error, str(e))
            return
        finally:
            self.call_from_thread(self._stream_end, full)

    def _stream_update(self, full: str) -> None:
        if self._stream_widget is not None:
            self._stream_widget.update(Markdown(full.strip(), code_theme="monokai"))
            self._scr().scroll_end()

    def _stream_error(self, msg: str) -> None:
        if self._stream_widget is not None:
            self._stream_widget.update(f"[red]{msg}[/red]")

    def _stream_end(self, full: str) -> None:
        if full and isinstance(full, str):
            self.messages.append({"role": "assistant", "content": full})
        self.streaming = False
        self._stream_widget = None
        inp = self.query_one("#inp", Input)
        inp.disabled = False
        inp.focus()

    def _agent_for_reply(self):
        from backend.agent import get_agent
        agent = get_agent(self.agent_name)
        if agent and not agent.model and self.model:
            agent.model = self.model
        return agent

    def _agent_history(self) -> list[dict]:
        return list(self.agent_histories.get(self.agent_name, []))

    def _provider_for_agent(self):
        from backend.provider.ollama import OllamaProvider
        return OllamaProvider(base_url=OLLAMA_HOST)

    @work(exclusive=True, group="chat")
    async def _run_agent_stream(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        await self._msg("user", text)
        w = await self._msg("assistant")
        self._stream_widget = w

        from backend.agent.runner import AgentRunner
        from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
        from backend.permission import PermissionEngine
        from backend.resilience import build_default_chain

        provider = self._provider_for_agent()
        agent = self._agent_for_reply()
        if not agent:
            w.update("[red]no agent configured[/red]")
            self.streaming = False
            self._stream_widget = None
            self.query_one("#inp", Input).disabled = False
            self.query_one("#inp", Input).focus()
            return

        runner = AgentRunner(
            provider=build_default_chain(provider, agent.name),
            agent=agent,
            tool_handlers=TOOL_HANDLERS,
            tool_schemas=TOOL_SCHEMAS,
            permission_engine=PermissionEngine.from_config(),
            on_ask=self._permission_ask,
            mcp=self._mcp_manager if self._mcp_ready else None,
            resilient=True,
        )

        full_content = ""
        try:
            async for ev in runner.run_stream(text, self._agent_history()):
                if ev["type"] == "delta":
                    full_content += ev["content"]
                    now = time.monotonic()
                    if now - self._last_update_time > 0.016:
                        w.update(Markdown(full_content.strip(), code_theme="monokai"))
                        self._scr().scroll_end()
                        self._last_update_time = now
                elif ev["type"] == "tool_call":
                    args_str = str(ev.get("args", {}))
                    await self._line(f"\u2192 {ev['name']}({args_str})", "dim italic")
                elif ev["type"] == "tool_result":
                    name = ev["name"]
                    ok = ev["ok"]
                    result = ev.get("result", "")
                    style = "green" if ok else "red"
                    await self._line(f"\u2190 {name}: {result[:200]}", style)
                elif ev["type"] == "error":
                    w.update(f"[red]{ev['message']}[/red]")
                    break
                elif ev["type"] == "done":
                    full_content = ev.get("content", full_content)
                    if full_content.strip():
                        w.update(Markdown(full_content.strip(), code_theme="monokai"))
                        self._scr().scroll_end()
                    break
        except Exception as e:
            w.update(f"[red]Error: {e}[/red]")
        finally:
            if full_content:
                self.messages.append({"role": "assistant", "content": full_content})
            self.agent_histories[self.agent_name] = list(self.messages)
            self.streaming = False
            self._stream_widget = None
            inp = self.query_one("#inp", Input)
            inp.disabled = False
            inp.focus()

    async def _permission_ask(self, agent_name: str, tool_name: str, target: str | None):
        from backend.tui.permission_modal import PermissionModal
        from backend.permission import Decision
        modal = PermissionModal(agent_name, tool_name, target)
        result = await self.app.push_screen(modal, wait_for_dismiss=True)
        if isinstance(result, tuple) and result[0] == "allow_always":
            return result[1]
        return result if isinstance(result, Decision) else Decision.DENY

    @work(exclusive=False, group="init", exit_on_error=False)
    async def _start_mcp(self) -> None:
        from backend.mcp.client import MCPManager, MCPError
        try:
            self._mcp_manager = MCPManager()
            results = await self._mcp_manager.connect_all()
            connected = sum(1 for v in results.values() if v)
            if connected:
                self._mcp_ready = True
        except Exception:
            pass

    async def action_quit(self) -> None:
        if self._mcp_manager:
            try:
                await self._mcp_manager.close_all()
            except Exception:
                pass
        self.exit()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        self.query_one("#inp", Input).clear()

        if text.startswith("/"):
            await self._slash(text)
            return

        if not self.model:
            await self._line("[red]No model selected (Ctrl+P)[/red]")
            return
        if self.streaming:
            await self._line("[yellow]still generating...[/yellow]")
            return

        self.streaming = True
        self.query_one("#inp", Input).disabled = True
        self._run_agent_stream(text)

    async def _slash(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        s = self._scr()

        if cmd == "/help":
            self.action_show_help()
        elif cmd == "/clear":
            await s.remove_children()
            self.messages = []
            self.agent_histories[self.agent_name] = []
            await self._line("cleared")
        elif cmd == "/model":
            if arg:
                self.model = arg
                self._bar()
                await self._line(f"model → {arg}")
            else:
                self.action_pick_model()
        elif cmd == "/system":
            self.system = arg
            shown = (arg[:60] + "...") if len(arg) > 60 else arg
            await self._line(f"system prompt set: {shown}")
        elif cmd in ("/agent", "/agents"):
            await self._agent_command(arg)
        elif cmd in ("/theme", "/themes"):
            if arg:
                self._set_theme(arg.strip())
                await self._line(f"theme → [bold]{arg.strip()}[/bold]")
            else:
                cur = getattr(self, "_theme_name", "apt-dark")
                avail = getattr(self, "_themes", ["apt-dark"])
                mark = lambda t: "*" if t == cur else " "
                await self._scr().mount(Static(
                    "[bold cyan]Themes[/bold cyan]\n" + "\n".join(f"  {mark(t)} {t}" for t in avail)
                ))
                self._scr().scroll_end()
                await self._line("[dim]Ctrl+T to cycle[/dim]")
        elif cmd == "/save":
            await self.action_save_session()
        elif cmd == "/load":
            await self.action_load_session()
        elif cmd == "/list":
            try:
                from backend.cookbook.persistence import list_sessions

                sessions = list_sessions()
                if sessions:
                    lines = "\n".join(
                        f"  {x['id'][:12]}  {x.get('model', ''):18}  {x.get('name', '')}"
                        for x in sessions
                    )
                    await s.mount(Static(f"[bold]Sessions:[/bold]\n{lines}"))
                else:
                    await self._line("no saved sessions")
                s.scroll_end()
            except Exception as e:
                await self._line(f"[red]{e}[/red]")
        elif cmd in ("/exit", "/quit"):
            self.exit()
        else:
            await self._line(f"[red]unknown command: {cmd}[/red]")

    async def _agent_command(self, arg: str) -> None:
        try:
            from backend.agent import list_agents, get_agent
        except Exception as e:
            await self._line(f"[red]agents unavailable: {e}[/red]")
            return
        if arg:
            a = get_agent(arg)
            if not a:
                await self._line(f"[red]no agent named '{arg}'[/red]")
                return
            self.agent_histories[self.agent_name] = list(self.messages)
            self.agent = a
            self.agent_name = a.name
            self.messages = list(self.agent_histories.get(self.agent_name, []))
            if a.system_prompt and not self.system:
                self.system = a.system_prompt
            s = self._scr()
            await s.remove_children()
            for m in self.messages:
                await self._msg(m["role"], m.get("content", ""))
            await self._line(
                f"agent → [bold]{a.name}[/bold]  [dim]tools: {', '.join(a.tools) or 'none'}[/dim]"
            )
            self._bar()
            return
        agents = list_agents()
        if not agents:
            await self._line("[yellow]no agents configured[/yellow]")
            return
        lines = []
        for a in agents:
            mark = "*" if a.name == self.agent_name else " "
            lines.append(f"  {mark} [bold]{a.name:9}[/bold] [{a.type:7}]  {a.description[:48]}")
        await self._scr().mount(Static(f"[bold cyan]Agents[/bold cyan]\n" + "\n".join(lines)))
        self._scr().scroll_end()


def run_tui() -> None:
    AptApp().run()
