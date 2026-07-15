#!/usr/bin/env python3

"""
LAC CLI — chat with and manage local LLMs via Ollama.

Subcommands:
  chat [model]          Interactive chat with a model
  list                  List installed models
  pull <model>          Download a model
  delete <model>        Delete a model
  ps                    Show running models
  inspect <model>       Show model details
  scan                  Scan hardware
  recommend             Get model recommendations
  browse [query]        Browse model library
  workspace             Manage workspaces
  config                View/set configuration
  help                  Show this help
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from backend.version import __version__
except ImportError:
    __version__ = "0.0.0"

C = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[91m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "blue": "\033[94m",
    "magenta": "\033[95m",
    "cyan": "\033[96m",
    "gray": "\033[90m",
}


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def get_host():
    return os.environ.get("OLLAMA_HOST", "http://localhost:11434")


def ollama(method, path, body=None, timeout=30):
    url = f"{get_host()}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except urllib.error.URLError:
        return {"error": f"Cannot connect to Ollama at {get_host()}"}
    except Exception as e:
        return {"error": str(e)}


def ollama_stream(path, body, timeout=300):
    url = f"{get_host()}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        for line in resp:
            decoded = line.decode().strip()
            if decoded:
                yield json.loads(decoded)
    except urllib.error.HTTPError as e:
        yield {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except urllib.error.URLError:
        yield {"error": f"Cannot connect to Ollama at {get_host()}"}
    except Exception as e:
        yield {"error": str(e)}


def print_header(text):
    print(f"\n{C['bold']}{C['blue']}{text}{C['reset']}")
    print(f"{C['blue']}{'-' * 48}{C['reset']}\n")


def print_table(headers, rows):
    cols = len(headers)
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))
    sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    print(sep)
    hdr = "| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |"
    print(f"{C['bold']}{hdr}{C['reset']}")
    print(sep)
    for row in rows:
        cells = "| " + " | ".join(
            str(c).ljust(col_widths[i]) for i, c in enumerate(row)
        ) + " |"
        print(cells)
    print(sep)


def _log_download(model_name: str, status: str = "completed", size_gb: float = 0):
    from backend.cookbook.downloads import log_download
    log_download(model_name, status, size_gb)


def _download_history() -> list[dict]:
    from backend.cookbook.downloads import download_history
    return download_history()


def _notify_model_installed(model_name: str) -> None:
    """Call every plugin's on_model_installed(model_name), isolated per-plugin
    (mirrors the register_cli mounting loop in build_parser()). Runs
    synchronously — a CLI session is already a blocking, watch-it-happen
    context, so a Pro autopilot hook prints its own progress inline."""
    from backend import plugins as _plugins
    try:
        found = _plugins.discover()
    except Exception as e:  # noqa: BLE001 — discovery failure must not kill the CLI
        eprint(f"[plugins] discovery failed: {e}")
        return
    for p in found:
        hook = getattr(p.obj, "on_model_installed", None)
        if not p.ok or hook is None:
            continue
        try:
            hook(model_name)
        except Exception as e:  # noqa: BLE001
            eprint(f"[plugin:{p.name}] on_model_installed failed: {e}")


def _update_session(session_id, model, messages):
    try:
        from backend.cookbook.persistence import save_session
        save_session(session_id, model, messages)
    except Exception:
        pass


def _auto_save(session_id, model, messages):
    if session_id and messages:
        ts = time.time()
        msgs = []
        for m in messages:
            msgs.append({**m, "timestamp": ts})
        _update_session(session_id, model, msgs)


def cmd_chat(args):
    from backend.cookbook.persistence import create_session, get_session, list_sessions, save_session
    from backend.cookbook.config import ensure_workspace

    ensure_workspace()
    models = ollama("GET", "/api/tags")
    if "error" in models:
        eprint(f"{C['red']}Error: {models['error']}{C['reset']}")
        sys.exit(1)

    model_list = [m["name"] for m in models.get("models", [])]
    if not model_list:
        eprint(f"{C['yellow']}No models installed. Pull one first: lac pull <model>{C['reset']}")
        sys.exit(1)

    model = args.model or model_list[0]
    if model not in model_list:
        eprint(f"{C['red']}Model '{model}' not installed. Available: {', '.join(model_list)}{C['reset']}")
        sys.exit(1)

    session_id = create_session(model=model)
    system_prompt = args.system or ""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
        print(f"{C['dim']}System: {system_prompt[:80]}{'...' if len(system_prompt) > 80 else ''}{C['reset']}")

    print(f"{C['green']}Chatting with {C['bold']}{model}{C['reset']}")
    print(f"{C['gray']}Session: {session_id}{C['reset']}")
    print(f"{C['gray']}Type /help for commands, Ctrl+C or /exit to quit.{C['reset']}\n")

    try:
        import readline
    except ImportError:
        pass

    while True:
        try:
            prompt = f"{C['cyan']}You> {C['reset']}"
            user_input = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        except UnicodeDecodeError:
            print(f"{C['red']}Input error. Try again.{C['reset']}")
            continue

        if not user_input:
            continue

        if user_input.startswith("/"):
            handled = handle_slash_command(user_input, model, messages, system_prompt, session_id)
            if handled == "exit":
                break
            elif handled == "continue":
                continue
            elif handled and handled.startswith("switch:"):
                model = handled[7:]
                print(f"{C['green']}Switched to {model}{C['reset']}")
                continue
            elif handled and handled.startswith("save:"):
                name = handled[5:]
                try:
                    save_session(session_id, model, messages, name=name)
                    print(f"{C['green']}Session saved as '{name}'.{C['reset']}")
                except Exception as e:
                    print(f"{C['red']}Failed to save session: {e}{C['reset']}")
                continue
            elif handled and handled.startswith("load:"):
                name = handled[5:]
                sessions = list_sessions()
                found = None
                for s in sessions:
                    if s["name"] == name or s["id"] == name:
                        found = s
                        break
                if found:
                    loaded = get_session(found["id"])
                    if loaded:
                        model = loaded["model"] or model
                        messages.clear()
                        for m in loaded.get("messages", []):
                            messages.append({"role": m["role"], "content": m["content"]})
                        session_id = found["id"]
                        print(f"{C['green']}Loaded session '{name}' with {len(messages)} messages.{C['reset']}")
                        print(f"{C['green']}Continuing chat with {model}.{C['reset']}")
                    else:
                        print(f"{C['red']}Could not load session '{name}'.{C['reset']}")
                else:
                    print(f"{C['red']}Session '{name}' not found.{C['reset']}")
                continue

        messages.append({"role": "user", "content": user_input})
        print(f"{C['green']}{model}>{C['reset']} ", end="", flush=True)

        full_response = ""
        for chunk in ollama_stream("/api/chat", {
            "model": model, "messages": messages, "stream": True
        }, timeout=args.timeout):
            if "error" in chunk:
                print(f"\n{C['red']}Error: {chunk['error']}{C['reset']}")
                break
            if chunk.get("message", {}).get("content"):
                content = chunk["message"]["content"]
                full_response += content
                print(content, end="", flush=True)
            if chunk.get("done"):
                break
        print()

        if full_response:
            messages.append({"role": "assistant", "content": full_response})

    _auto_save(session_id, model, messages)


def handle_slash_command(cmd, model, messages, system_prompt, session_id=None):
    cmd_str = cmd.strip()
    cmd_lower = cmd_str.strip().lower()

    if cmd_lower in ("/exit", "/quit"):
        print(f"{C['yellow']}Goodbye!{C['reset']}")
        return "exit"

    if cmd_lower in ("/help", "/h"):
        print(f"\n{C['bold']}Commands:{C['reset']}")
        cmds = [
            ("/help", "Show this help"),
            ("/clear", "Clear conversation"),
            ("/model <name>", "Switch model"),
            ("/system <prompt>", "Set system prompt"),
            ("/info", "Show current model info"),
            ("/tokens", "Estimate token usage"),
            ("/save <name>", "Save conversation"),
            ("/load <name>", "Load saved session"),
            ("/list", "List saved sessions"),
            ("/delete <name>", "Delete saved session"),
            ("/copy", "Copy last response"),
            ("/exit", "Exit chat"),
        ]
        for c, desc in cmds:
            print(f"  {C['cyan']}{c:<20}{C['reset']} {C['gray']}{desc}{C['reset']}")
        return "continue"

    if cmd_lower == "/clear":
        messages.clear()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        print(f"{C['yellow']}Conversation cleared.{C['reset']}")
        return "continue"

    if cmd_lower.startswith("/model "):
        new_model = cmd_lower[7:].strip()
        info = ollama("GET", "/api/tags")
        available = [m["name"] for m in info.get("models", [])]
        if new_model in available:
            messages.clear()
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            return f"switch:{new_model}"
        else:
            print(f"{C['red']}Model '{new_model}' not installed.{C['reset']}")
            return "continue"

    if cmd_lower.startswith("/system "):
        sp = cmd_str[8:].strip()
        if sp:
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] = sp
            else:
                messages.insert(0, {"role": "system", "content": sp})
            print(f"{C['yellow']}System prompt set.{C['reset']}")
        else:
            print(f"{C['yellow']}Current system prompt: {messages[0]['content'] if messages and messages[0]['role'] == 'system' else '(none)'}{C['reset']}")
        return "continue"

    if cmd_lower == "/info":
        info = ollama("POST", "/api/show", {"name": model})
        if "error" in info:
            print(f"{C['red']}{info['error']}{C['reset']}")
        else:
            print(f"\n{C['bold']}Model: {model}{C['reset']}")
            details = info.get("details", {})
            print(f"  Parameters: {details.get('parameter_size', '?')}")
            print(f"  Quantization: {details.get('quantization_level', '?')}")
            print(f"  Family: {details.get('family', '?')}")
            print(f"  Format: {details.get('format', '?')}")
            modelfile = info.get("modelfile", "")
            for line in modelfile.split("\n"):
                if "CONTEXT_LENGTH" in line:
                    print(f"  Context: {line.split()[1]}")
        return "continue"

    if cmd_lower == "/tokens":
        total = sum(len(m["content"].split()) * 1.3 for m in messages)
        print(f"{C['yellow']}Estimated: ~{int(total)} tokens ({len(messages)} messages){C['reset']}")
        return "continue"

    if cmd_lower == "/copy":
        for m in reversed(messages):
            if m["role"] == "assistant":
                try:
                    import pyperclip
                    pyperclip.copy(m["content"])
                    print(f"{C['green']}Copied last response to clipboard.{C['reset']}")
                except ImportError:
                    print(f"{C['yellow']}pyperclip not installed. Response length: {len(m['content'])} chars{C['reset']}")
                break
        return "continue"

    if cmd_lower.startswith("/save "):
        name = cmd_str[6:].strip()
        return f"save:{name}"

    if cmd_lower == "/list":
        try:
            from backend.cookbook.persistence import list_sessions
            sessions = list_sessions()
            if not sessions:
                print(f"{C['yellow']}No saved sessions.{C['reset']}")
            else:
                print(f"\n{C['bold']}Saved Sessions:{C['reset']}")
                for s in sessions:
                    sname = s["name"] or s["id"]
                    smodel = s["model"] or "?"
                    print(f"  {C['cyan']}{sname:<24}{C['reset']}  model={smodel}")
        except Exception as e:
            print(f"{C['red']}Error listing sessions: {e}{C['reset']}")
        return "continue"

    if cmd_lower.startswith("/load "):
        name = cmd_str[6:].strip()
        return f"load:{name}"

    if cmd_lower.startswith("/delete "):
        name = cmd_str[8:].strip()
        try:
            from backend.cookbook.persistence import list_sessions, delete_session
            sessions = list_sessions()
            for s in sessions:
                if s["name"] == name or s["id"] == name:
                    delete_session(s["id"])
                    print(f"{C['green']}Deleted session '{name}'.{C['reset']}")
                    break
            else:
                print(f"{C['red']}Session '{name}' not found.{C['reset']}")
        except Exception as e:
            print(f"{C['red']}Error: {e}{C['reset']}")
        return "continue"

    print(f"{C['red']}Unknown command: {cmd_lower}{C['reset']}")
    return "continue"


def cmd_list(args):
    result = ollama("GET", "/api/tags")
    if "error" in result:
        eprint(f"{C['red']}{result['error']}{C['reset']}")
        sys.exit(1)

    models = result.get("models", [])
    if not models:
        print(f"{C['yellow']}No models installed. Pull one: lac pull <model>{C['reset']}")
        return

    print_header(f"Installed Models ({len(models)})")
    headers = ["Name", "Size", "Modified"]
    rows = []
    for m in sorted(models, key=lambda x: x["name"]):
        size_gb = round(m.get("size", 0) / (1024**3), 2)
        modified = m.get("modified_at", "")[:10]
        rows.append([m["name"], f"{size_gb} GB", modified])
    print_table(headers, rows)


def cmd_pull(args):
    model = args.model
    print(f"{C['yellow']}Pulling {C['bold']}{model}{C['reset']}...")
    print(f"{C['gray']}(this may take a while depending on model size){C['reset']}\n")

    success = False
    last_total = 0
    for chunk in ollama_stream("/api/pull", {"name": model}, timeout=3600):
        if "error" in chunk:
            eprint(f"\n{C['red']}Error: {chunk['error']}{C['reset']}")
            _log_download(model, "failed")
            sys.exit(1)
        status = chunk.get("status", "")
        if status:
            completed = chunk.get("completed", 0)
            total = chunk.get("total", 0)
            if total:
                last_total = total
            if total and completed:
                pct = int(completed / total * 100)
                bar = "█" * (pct // 2) + "░" * (50 - pct // 2)
                print(f"\r{C['cyan']}[{bar}]{C['reset']} {pct}% - {status}", end="", flush=True)
            else:
                print(f"\r  {C['dim']}{status}{C['reset']}", end="", flush=True)
        if chunk.get("status") == "success":
            success = True
            # The terminal "success" chunk never carries 'total' itself --
            # use the last real total seen during the download.
            size_gb = round(last_total / (1024**3), 2) if last_total else 0
            print(f"\n\n{C['green']}✓ {model} installed successfully!{C['reset']}")
            _log_download(model, "completed", size_gb)
            _notify_model_installed(model)

    if not success:
        print(f"\n{C['yellow']}Pull may still be in progress. Check 'lac list'.{C['reset']}")
        _log_download(model, "incomplete")


def cmd_delete(args):
    model = args.model
    if not args.yes:
        print(f"{C['yellow']}Delete {C['bold']}{model}{C['reset']}? [y/N] ", end="", flush=True)
        resp = input().strip().lower()
        if resp != "y":
            print(f"{C['gray']}Cancelled.{C['reset']}")
            return
    result = ollama("DELETE", f"/api/delete", {"name": model})
    if "error" in result:
        eprint(f"{C['red']}{result['error']}{C['reset']}")
        sys.exit(1)
    print(f"{C['green']}✓ {model} deleted.{C['reset']}")


def cmd_ps(args):
    result = ollama("GET", "/api/ps")
    if "error" in result:
        eprint(f"{C['red']}{result['error']}{C['reset']}")
        sys.exit(1)

    models = result.get("models", [])
    if not models:
        print(f"{C['yellow']}No models currently loaded in memory.{C['reset']}")
        return

    print_header(f"Running Models ({len(models)})")
    headers = ["Name", "Size", "VRAM"]
    rows = []
    for m in models:
        size_gb = round(m.get("size", 0) / (1024**3), 2)
        vram = m.get("size_vram", 0)
        vram_gb = round(vram / (1024**3), 2) if vram else 0
        rows.append([m["name"], f"{size_gb} GB", f"{vram_gb} GB" if vram_gb else "?"])
    print_table(headers, rows)


def cmd_inspect(args):
    model = args.model
    result = ollama("POST", f"/api/show", {"name": model})
    if "error" in result:
        eprint(f"{C['red']}{result['error']}{C['reset']}")
        sys.exit(1)

    # /api/show has no top-level 'size' field -- only /api/tags does.
    size_bytes = 0
    tags = ollama("GET", "/api/tags")
    if "error" not in tags:
        for m in tags.get("models", []):
            if m.get("name") == model:
                size_bytes = m.get("size", 0)
                break

    print_header(f"Model: {model}")
    details = result.get("details", {})
    info_rows = [
        ["Parameters", details.get("parameter_size", "?")],
        ["Quantization", details.get("quantization_level", "?")],
        ["Family", details.get("family", "?")],
        ["Format", details.get("format", "?")],
        ["Size", f"{round(size_bytes / (1024**3), 2)} GB"],
        ["Modified", result.get("modified_at", "?")],
    ]
    modelfile = result.get("modelfile", "")
    for line in modelfile.split("\n"):
        if line.startswith("FROM "):
            info_rows.append(["Base", line[5:].strip()])
        if "CONTEXT_LENGTH" in line:
            try:
                info_rows.append(["Context", line.split()[1]])
            except IndexError:
                pass

    for label, value in info_rows:
        print(f"  {C['bold']}{label}:{C['reset']} {value}")
    print()


def cmd_agents(args):
    from backend.agent import list_agents, get_agent

    agents = list_agents()
    print_header(f"LAC Agents ({len(agents)})")
    for a in agents:
        p = a.permissions
        flags = []
        flags.append("R" if p.can_read() else "-")
        flags.append("W" if p.can_write() else "-")
        flags.append("D" if p.can_delete() else "-")
        flags.append("$" if p.can_run_bash() else "-")
        flags.append("N" if p.can_fetch() else "-")
        flags.append("M" if p.can_mcp() else "-")
        print(f"  {C['bold']}{a.name:9}{C['reset']} [{a.type:7}] perms=[{''.join(flags)}]  {C['dim']}{a.description[:60]}{C['reset']}")
        if a.tools:
            print(f"            tools: {', '.join(a.tools)}")
    print()
    print(f"{C['dim']}Switch in TUI with: /agent <name>{C['reset']}")


def cmd_providers(args):
    from backend.provider import list_providers, create_provider, default_provider
    from backend.provider.base import ProviderError

    provs = list_providers()
    print_header(f"LLM Providers ({len(provs)})")
    headers = ["Name", "Type", "Configured"]
    rows = []
    for p in provs:
        rows.append([p["name"], p["type"], "yes" if p["configured"] else "no"])
    print_table(headers, rows)
    print()
    try:
        default = default_provider()
        models = default.list_models()
        names = ", ".join(m.name for m in models)
        print(f"{C['dim']}Default provider: {default.name} ({default.display_name}){C['reset']}")
        print(f"{C['dim']}Models ({len(models)}): {names}{C['reset']}")
    except ProviderError as e:
        eprint(f"{C['red']}{e}{C['reset']}")


def cmd_mcp(args):
    import asyncio
    from backend.config import resolve_config
    from backend.mcp import create_manager

    cfg = resolve_config()
    servers = cfg.mcp_servers
    print_header(f"MCP Servers ({len(servers)})")
    if not servers:
        print(f"{C['dim']}No MCP servers configured in .apt/apt.jsonc{C['reset']}")
        return
    if args.action == "list":
        for name, sc in servers.items():
            print(f"  {C['bold']}{name:14}{C['reset']} {sc.transport:6} {sc.command or sc.url}")
        return

    if args.action == "tools":
        mgr = create_manager()

        async def _go():
            try:
                await mgr.connect_all()
                for name in servers:
                    st = mgr.state(name)
                    if not st or not st.connected:
                        print(f"  {name}: {C['red']}not connected: {st.error if st else '?'}{C['reset']}")
                        continue
                    tools = await mgr.list_tools(name)
                    print(f"  {C['bold']}{name}{C['reset']} ({len(tools)} tools):")
                    for t in tools:
                        print(f"      - {t['name']:24} {C['dim']}{t['description'][:50]}{C['reset']}")
            finally:
                await mgr.close_all()

        asyncio.run(_go())


def cmd_openapi(args):
    from backend.api import app
    from backend.openapi_gen import write_openapi

    out = args.output or "openapi.json"
    path = write_openapi(app, out)
    import json

    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    n_paths = len(spec.get("paths", {}))
    n_ops = sum(len(v) for v in spec.get("paths", {}).values())
    print(f"{C['green']}Wrote {path}{C['reset']}  {C['dim']}({n_paths} paths, {n_ops} operations, openapi {spec['openapi']}){C['reset']}")


def cmd_session(args):
    from backend.cookbook.persistence import get_session, list_sessions, create_session, save_session
    from backend.cookbook.export import export_session_file, export_all, import_session

    action = args.action
    if action == "list":
        sessions = list_sessions()
        print_header(f"Sessions ({len(sessions)})")
        if not sessions:
            print(f"{C['dim']}no saved sessions{C['reset']}")
            return
        for s in sessions:
            print(f"  {C['bold']}{s['id'][:12]}{C['reset']}  {s.get('model',''):18}  {_ts(s.get('updated_at'))}  {s.get('name','')}")
        return

    if action == "export":
        fmt = (args.format or "md").lower()
        if args.all:
            out = args.out or "./exports"
            written = export_all(out, fmt)
            print(f"{C['green']}Exported {len(written)} file(s) to {out}{C['reset']}")
            return
        if not args.id:
            eprint(f"{C['red']}session export requires <id> or --all{C['reset']}")
            sys.exit(1)
        session = get_session(args.id)
        if not session:
            eprint(f"{C['red']}session not found: {args.id}{C['reset']}")
            sys.exit(1)
        path = export_session_file(session, fmt, args.out)
        print(f"{C['green']}Wrote {path}{C['reset']}  {C['dim']}({fmt}, {len(session.get('messages', []))} messages){C['reset']}")
        return

    if action == "import":
        if not args.path:
            eprint(f"{C['red']}session import requires <path>{C['reset']}")
            sys.exit(1)
        try:
            data = import_session(args.path)
        except FileNotFoundError:
            eprint(f"{C['red']}File not found: {args.path}{C['reset']}")
            sys.exit(1)
        sid = data.get("id") or create_session(model=data.get("model", ""))
        save_session(sid, model=data.get("model", ""), messages=data.get("messages", []))
        print(f"{C['green']}Imported session {sid[:12]}{C['reset']}  {C['dim']}({len(data.get('messages', []))} messages, model={data.get('model','')}){C['reset']}")
        return


def _ts(epoch):
    import datetime as _dt
    if not epoch:
        return ""
    try:
        return _dt.datetime.fromtimestamp(float(epoch)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def cmd_update(args):
    from backend.update import UpdateMode, check_update, detect_install_method, do_update, configured_mode

    method = detect_install_method()
    mode = UpdateMode.parse(args.mode) if args.mode else configured_mode()
    print_header(f"LAC Update  [dim]({method}, {mode.value})[/dim]")

    if args.action == "install":
        result = do_update(UpdateMode.ENABLE)
    else:
        info = check_update()
        if info is None:
            print(f"{C['green']}LAC is up to date.{C['reset']}  {C['dim']}v{_ver()}{C['reset']}")
            return
        print(f"{C['yellow']}Update available: v{info['latest_version']}{C['reset']}  {C['dim']}(current v{info['current_version']}){C['reset']}")
        if info.get("changelog"):
            print(f"\n{C['dim']}{info['changelog'][:600]}{C['reset']}\n")
        print(f"{C['dim']}Apply with: lac update install{C['reset']}")
        return

    if result.get("update_available") is False and not args.action == "install":
        print(f"{C['green']}Up to date.{C['reset']}")
        return
    if result.get("applied"):
        print(f"{C['green']}Updated to latest.{C['reset']}")
    elif result.get("error"):
        print(f"{C['red']}{result['error']}{C['reset']}")
    else:
        print(f"{C['yellow']}No update applied.{C['reset']}")


def _ver():
    try:
        from backend.version import __version__
        return __version__
    except Exception:
        return "0.0.0"


def cmd_scan(args):
    print(f"{C['yellow']}Scanning hardware...{C['reset']}")
    script_dir = Path(__file__).parent
    sys.path.insert(0, str(script_dir))
    try:
        from backend.cookbook.hardware import detect
        info = detect()
        print_header("System")

        rows = [
            ["OS", info.os],
            ["CPU", info.cpu],
            ["Cores", str(info.cpu_cores)],
            ["RAM", f"{info.ram_gb} GB"],
        ]
        if info.gpus:
            for i, gpu in enumerate(info.gpus):
                rows.append([f"GPU {i+1}", f"{gpu.name} ({gpu.vram_gb} GB, {gpu.backend}, {gpu.tier})"])
        else:
            rows.append(["GPU", "None detected"])
        if info.is_apple_silicon:
            rows.append(["Apple Silicon", "Yes"])

        for label, value in rows:
            print(f"  {C['bold']}{label}:{C['reset']} {value}")
        print()

        total_vram = info.total_vram_gb or (info.gpus[0].vram_gb if info.gpus else 0)
        if total_vram:
            print(f"  {C['bold']}Total VRAM:{C['reset']} {total_vram} GB")
            print(f"  {C['bold']}Models that fit:{C['reset']} Up to ~{int(total_vram / 0.58 * 0.9)}B params at Q4_K_M")

        # Hand-off: show combined GPU VRAM and tier breakdown.
        if info.combined_vram_gb > info.total_vram_gb + 0.1:
            extra = info.combined_vram_gb - info.total_vram_gb
            print(f"\n  {C['bold']}Hand-off (multi-GPU):{C['reset']} {info.combined_vram_gb} GB combined GPU VRAM (+{extra:.1f} GB)")
            for t in info.compute_tiers:
                tag = {"discrete": "dGPU", "integrated": "iGPU", "ram": "RAM"}.get(t.kind, t.kind)
                print(f"    {C['gray']}{tag:<6} {t.name} ({t.memory_gb} GB, {t.backend}){C['reset']}")

    except ImportError as e:
        eprint(f"{C['red']}Error loading hardware scanner: {e}{C['reset']}")
        eprint(f"{C['gray']}Run from the LAC project directory.{C['reset']}")
        sys.exit(1)


def cmd_recommend(args):
    script_dir = Path(__file__).parent
    sys.path.insert(0, str(script_dir))
    try:
        from backend.cookbook.hardware import detect
        from backend.cookbook.recommend import recommend

        top_k = args.top_k if args.top_k is not None else 10
        if top_k < 1:
            eprint(f"{C['red']}--top-k must be a positive integer (got {top_k}).{C['reset']}")
            sys.exit(1)

        print(f"{C['yellow']}Scanning hardware and computing recommendations...{C['reset']}")
        info = detect()
        use_case = args.use_case or "coding"

        if getattr(args, "no_calibration", False):
            _cal = None
        else:
            from backend.cookbook.calibration import load_calibration, detect_stack
            _stack = detect_stack(info=info)
            _results = str(Path.home() / ".model-hub" / "benchmarks" / "results.jsonl")
            _cal = load_calibration(info, _stack, _results)

        recs = recommend(info, use_case=use_case, top_k=top_k, calibration=_cal)

        if not recs:
            print(f"{C['yellow']}No models fit your hardware.{C['reset']}")
            return

        print_header(f"Top {len(recs)} Recommendations for '{use_case}'")
        headers = ["#", "Model", "Quant", "Score", "VRAM", "Ctx", "Mode"]
        rows = []
        for i, r in enumerate(recs, 1):
            if r.run_mode == "gpu":
                mode = f"{C['green']}GPU{C['reset']}"
            elif r.run_mode == "multi_gpu":
                mode = f"{C['blue']}Multi-GPU{C['reset']}"
            else:
                mode = f"{C['yellow']}Offload{C['reset']}"
            rows.append([str(i), r.model.name, r.quant, str(r.score), f"{r.vram_gb} GB", str(r.context_used), mode])

        for row in rows:
            print(f"  {row[0]:<3} {C['bold']}{row[1]:<35}{C['reset']} {row[2]:<7} {row[3]:<7} {row[4]:<7} {row[5]:<7} {row[6]}")

        # Show split-plan detail for top 3 multi-GPU / offload picks.
        print()
        for i, r in enumerate(recs[:3], 1):
            if r.split_plan and r.run_mode != "gpu":
                env = ""
                if r.split_plan.env_vars:
                    env = "  " + " ".join(f"{k}={v}" for k, v in r.split_plan.env_vars.items())
                print(f"  {C['gray']}{i}. {r.split_plan.summary}{env}{C['reset']}")

    except ImportError as e:
        eprint(f"{C['red']}Error: {e}{C['reset']}")
        eprint(f"{C['gray']}Run from the LAC project directory.{C['reset']}")
        sys.exit(1)


def cmd_agent(args):
    script_dir = Path(__file__).parent
    sys.path.insert(0, str(script_dir))
    try:
        from backend.agent_launch.launcher import launch_agent
        from backend.agent_launch.opencode_bin import OpenCodeNotFound
        from backend.agent_launch.variant import BaseModelNotInstalled
    except ImportError as e:
        eprint(f"{C['red']}Error: {e}{C['reset']}")
        sys.exit(1)
    try:
        rc = launch_agent(Path(args.dir))
    except (OpenCodeNotFound, BaseModelNotInstalled) as e:
        eprint(f"{C['yellow']}{e}{C['reset']}")
        sys.exit(1)
    sys.exit(rc)


def cmd_browse(args):
    query = args.query or ""
    sort = args.sort or "pulls"

    models = []
    cache_path = Path(__file__).parent / "backend" / "cookbook" / "data" / "library_cache.json"
    if cache_path.exists():
        try:
            with open(cache_path) as f:
                data = json.load(f)
            models = data.get("models", [])
        except Exception:
            pass

    if not models:
        try:
            resp = ollama("GET", "/api/tags")
            if resp and "models" in resp:
                local_models = resp["models"]
                models = [{"name": m["name"], "display": m["name"], "params_b": 0, "vram_q4": 0, "vram_q8": 0, "context": 0, "pulls": "0", "description": "Installed locally"} for m in local_models]
        except Exception:
            pass

    if not models:
        eprint(f"{C['yellow']}No model catalog available.{C['reset']}")
        sys.exit(1)

    system_vram = None
    try:
        from backend.cookbook.hardware import detect
        info = detect()
        system_vram = info.total_vram_gb or (info.gpus[0].vram_gb if info.gpus else 0)
    except Exception:
        system_vram = None

    from backend.cookbook.library import enrich_library_models
    models = enrich_library_models(models, system_vram)

    if query:
        q = query.lower()
        models = [m for m in models if q in m.get("display", m.get("name", "")).lower() or q in m.get("description", "").lower()]

    models = [m for m in models if m.get("vram_q4", 0) > 0]

    if sort == "pulls":
        def parse_pulls(p):
            try:
                p = p.replace("M", "e6").replace("B", "e9").replace("K", "e3")
                return float(p)
            except (ValueError, TypeError):
                return 0
        models.sort(key=lambda m: parse_pulls(m.get("pulls", "0")), reverse=True)
    elif sort == "vram":
        models.sort(key=lambda m: m.get("vram_q4", 999))
    elif sort == "params":
        models.sort(key=lambda m: m.get("params_b", 0), reverse=True)
    elif sort == "name":
        models.sort(key=lambda m: m.get("display", m.get("name", "")))

    print_header(f"Model Library ({len(models)} variants)")
    for m in models[:args.limit or 30]:
        display = m.get("display", m.get("name", "?"))
        params = m.get("params_b", 0)
        vram_q4 = m.get("vram_q4", 0)
        vram_q8 = m.get("vram_q8", 0)
        pulls = m.get("pulls", "0")
        ctx = m.get("context", 0)
        if vram_q4:
            color = C["green"] if vram_q4 <= 16 else (C["yellow"] if vram_q4 <= 32 else C["red"])
            print(f"  {C['bold']}{display:<40}{C['reset']} {color}{vram_q4:>5.1f}GB Q4{C['reset']}  {C['dim']}{vram_q8:>5.1f}GB Q8  {params:>5.1f}B  ctx={ctx:<6}  pulls={pulls}{C['reset']}")
        else:
            print(f"  {C['gray']}{display:<40}  no VRAM data{C['reset']}")

    remaining = len(models) - (args.limit or 30)
    if remaining > 0:
        print(f"\n  {C['dim']}... and {remaining} more. Use --limit to show more.{C['reset']}")


def cmd_workspace(args):
    from backend.cookbook.config import (
        list_workspaces, create_workspace, delete_workspace,
        switch_workspace, get_workspace, load_config,
    )

    if args.action == "list":
        ws = list_workspaces()
        config = load_config()
        if not ws:
            print(f"{C['yellow']}No workspaces.{C['reset']}")
            return
        print_header(f"Workspaces ({len(ws)})")
        for w in ws:
            marker = f"{C['green']}*{C['reset']}" if w.id == config.workspace else " "
            print(f"  {marker} {C['bold']}{w.name:<30}{C['reset']} {C['gray']}{w.description}{C['reset']}")

    elif args.action == "create":
        name = args.name
        desc = args.description or ""
        if not name:
            eprint(f"{C['red']}Workspace name required.{C['reset']}")
            sys.exit(1)
        try:
            ws = create_workspace(name, desc)
        except ValueError as e:
            eprint(f"{C['red']}{e}{C['reset']}")
            sys.exit(1)
        print(f"{C['green']}✓ Created workspace '{ws.name}' (id: {ws.id}){C['reset']}")

    elif args.action == "delete":
        name = args.name
        ws = get_workspace(name)
        if not ws:
            eprint(f"{C['red']}Workspace '{name}' not found.{C['reset']}")
            sys.exit(1)
        if not args.yes:
            print(f"{C['yellow']}Delete workspace '{ws.name}'? This removes all sessions. [y/N] ", end="", flush=True)
            resp = input().strip().lower()
            if resp != "y":
                print(f"{C['gray']}Cancelled.{C['reset']}")
                return
        if delete_workspace(name):
            print(f"{C['green']}✓ Deleted workspace '{name}'.{C['reset']}")
        else:
            from backend.cookbook.persistence import list_projects

            if list_projects(name):
                print(
                    f"{C['red']}Cannot delete workspace '{name}' while it has "
                    f"registered projects.{C['reset']}"
                )
            else:
                print(f"{C['red']}Cannot delete the default workspace.{C['reset']}")

    elif args.action == "switch":
        name = args.name
        if switch_workspace(name):
            print(f"{C['green']}✓ Switched to workspace '{name}'.{C['reset']}")
        else:
            eprint(f"{C['red']}Workspace '{name}' not found.{C['reset']}")
            sys.exit(1)

    elif args.action == "show":
        from backend.cookbook.config import load_config
        config = load_config()
        ws = get_workspace(config.workspace)
        ws_name = ws.name if ws else config.workspace
        print_header(f"Current Workspace")
        print(f"  {C['bold']}ID:{C['reset']}   {config.workspace}")
        print(f"  {C['bold']}Name:{C['reset']} {ws_name}")


def cmd_config(args):
    from backend.cookbook.config import load_config, save_config

    if args.action == "show":
        cfg = load_config()
        print_header("Configuration")
        print(f"  {C['bold']}workspace:{C['reset']}    {cfg.workspace}")
        print(f"  {C['bold']}ollama_host:{C['reset']}  {cfg.ollama_host}")
        print(f"  {C['bold']}theme:{C['reset']}        {cfg.theme}")
        print(f"  {C['bold']}default_model:{C['reset']} {cfg.default_model}")
        print()

    elif args.action == "set":
        key = args.key
        value = args.value
        cfg = load_config()
        valid_keys = {"workspace", "ollama_host", "theme", "default_model"}
        if key not in valid_keys:
            eprint(f"{C['red']}Invalid key: {key}. Valid: {', '.join(sorted(valid_keys))}{C['reset']}")
            sys.exit(1)
        setattr(cfg, key, value)
        save_config(cfg)
        print(f"{C['green']}✓ Set {key} = {value}{C['reset']}")

    elif args.action == "downloads":
        history = _download_history()
        if not history:
            print(f"{C['yellow']}No download history.{C['reset']}")
            return
        print_header(f"Download History ({len(history)} entries)")
        for entry in reversed(history[-30:]):
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.get("timestamp", 0)))
            status = entry.get("status", "?")
            size = f" ({entry['size_gb']} GB)" if entry.get("size_gb") else ""
            print(f"  {C['dim']}{ts}{C['reset']}  {C['bold']}{entry['model']:<30}{C['reset']}  {status}{size}")
        print()


def cmd_plugins(args):
    from backend import plugins as _plugins
    found = _plugins.discover()
    print_header("Plugins")
    if not found:
        print("  No plugins installed. Pro and community plugins mount here.")
        return
    rows = []
    for p in found:
        issue = p.error or p.compatibility_error or "unavailable"
        status = "ok" if p.ok else f"{p.state}: {issue}"
        rows.append([p.name, p.version, status])
    print_table(["Name", "Version", "Status"], rows)


def cmd_unlock(args):
    """Activate LAC Pro: install the licensed plugin, then activate the seat.

    Exit codes: 0 = activated, 1 = any failure (honest message on stderr).
    """
    from backend import pro_install
    from backend import self_invoke
    from backend.cookbook import proc

    result = pro_install.install_pro_plugin(args.key)
    if result.get("state") == "installed":
        dest = result.get("path", "the LAC plugin directory")
        try:
            r = proc.run(
                [*self_invoke.cli_prefix(), "pro", "activate"],
                input=args.key + "\n",
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception as exc:  # noqa: BLE001
            eprint(f"{C['red']}Pro installed to {dest}, but activation could not run: {exc}{C['reset']}")
            eprint("  Restart LAC, then run: lac pro activate <key>")
            sys.exit(1)

        if r.returncode != 0:
            lines = (r.stdout or r.stderr or "activation failed").strip().splitlines()
            msg = lines[-1].strip() if lines else "activation failed"
            eprint(f"{C['red']}Pro installed to {dest}, but activation failed: {msg}{C['reset']}")
            eprint("  Restart LAC, then run: lac pro activate <key>")
            sys.exit(1)

        print(f"{C['green']}LAC Pro installed and activated on this machine{C['reset']}")
        print(f"  Plugin installed to {dest}")
        print("  Restart LAC to load the Pro cockpit.")
        return

    eprint(f"{C['red']}✗ Unlock failed: {result.get('message', 'unknown error')}{C['reset']}")
    sys.exit(1)


def print_banner():
    print()
    print(f"  {C['bold']}{C['green']}lac{C['reset']} {C['dim']}v{__version__}  ·  Local AI, sorted.{C['reset']}")
    print()


def build_parser():
    parser = argparse.ArgumentParser(
        prog="lac",
        description=f"LAC CLI v{__version__} — Find your perfect local LLM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default=None, help="Ollama host (default: OLLAMA_HOST env or localhost:11434)")

    sub = parser.add_subparsers(dest="command")

    p_chat = sub.add_parser("chat", aliases=["tui"], help="Launch LAC TUI — full terminal chat interface")
    p_chat.add_argument("model", nargs="?", help="Model to use (default: first installed)")
    p_chat.add_argument("--system", help="System prompt")
    p_chat.add_argument("--timeout", type=int, default=300, help="Request timeout in seconds (legacy only)")

    p_list = sub.add_parser("list", aliases=["ls"], help="List installed models")
    p_pull = sub.add_parser("pull", help="Download a model")
    p_pull.add_argument("model", help="Model name (e.g. llama3.2:3b)")
    p_delete = sub.add_parser("delete", aliases=["rm"], help="Delete a model")
    p_delete.add_argument("model", help="Model name")
    p_delete.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")

    p_ps = sub.add_parser("ps", help="Show running models")
    p_inspect = sub.add_parser("inspect", help="Show model details")
    p_inspect.add_argument("model", help="Model name")

    p_scan = sub.add_parser("scan", help="Scan hardware")

    p_agents = sub.add_parser("agents", help="List configured agents and permissions")
    p_providers = sub.add_parser("providers", help="List LLM providers")
    p_mcp = sub.add_parser("mcp", help="Inspect MCP servers")
    p_mcp.add_argument("action", nargs="?", choices=["list", "tools"], default="list", help="list or tools")

    p_openapi = sub.add_parser("openapi", help="Generate OpenAPI 3.1 spec from Flask routes")
    p_openapi.add_argument("-o", "--output", default="openapi.json", help="Output file path")

    p_session = sub.add_parser("session", help="Export/import chat sessions")
    p_session_sub = p_session.add_subparsers(dest="action", required=True)
    p_slist = p_session_sub.add_parser("list", help="List saved sessions")
    p_sexport = p_session_sub.add_parser("export", help="Export a session")
    p_sexport.add_argument("id", nargs="?", help="Session id (or use --all)")
    p_sexport.add_argument("--format", "-f", default="md", choices=["md", "json", "yaml", "html", "opencode-json"], help="Output format")
    p_sexport.add_argument("--out", "-o", help="Output dir (default cwd)")
    p_sexport.add_argument("--all", action="store_true", help="Export all sessions")
    p_simport = p_session_sub.add_parser("import", help="Import a session")
    p_simport.add_argument("path", help="Path to session json/yaml")

    p_update = sub.add_parser("update", help="Check for or apply LAC updates")
    p_update.add_argument("action", nargs="?", choices=["check", "install"], default="check", help="check or install")
    p_update.add_argument("--mode", choices=["enable", "disable", "check-only"], help="Override update mode")

    p_rec = sub.add_parser("recommend", aliases=["rec"], help="Get model recommendations")
    p_rec.add_argument("--use-case", default="coding", choices=["coding", "general", "reasoning", "chat", "agent"], help="Use case")
    p_rec.add_argument("--top-k", type=int, default=10, help="Number of recommendations")
    p_rec.add_argument("--no-calibration", action="store_true", help="Ignore measured benchmarks in results.jsonl")

    p_agent = sub.add_parser("agent", help="Launch the LAC local-model coding agent (OpenCode + hardware brain)")
    p_agent.add_argument("dir", nargs="?", default=".", help="Project directory (default: current)")

    p_browse = sub.add_parser("browse", help="Browse model library")
    p_browse.add_argument("query", nargs="?", help="Search query")
    p_browse.add_argument("--sort", default="pulls", choices=["pulls", "vram", "params", "name"], help="Sort order")
    p_browse.add_argument("--limit", type=int, default=30, help="Max results")

    p_ws = sub.add_parser("workspace", aliases=["ws"], help="Manage workspaces")
    ws_sub = p_ws.add_subparsers(dest="action", required=True)
    ws_list = ws_sub.add_parser("list", help="List workspaces")
    ws_show = ws_sub.add_parser("show", help="Show current workspace")
    ws_create = ws_sub.add_parser("create", help="Create a workspace")
    ws_create.add_argument("name", help="Workspace name")
    ws_create.add_argument("--description", "-d", help="Workspace description")
    ws_delete = ws_sub.add_parser("delete", help="Delete a workspace")
    ws_delete.add_argument("name", help="Workspace name or id")
    ws_delete.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
    ws_switch = ws_sub.add_parser("switch", help="Switch workspace")
    ws_switch.add_argument("name", help="Workspace name or id")

    p_cfg = sub.add_parser("config", help="View or set configuration")
    cfg_sub = p_cfg.add_subparsers(dest="action", required=True)
    cfg_show = cfg_sub.add_parser("show", help="Show configuration")
    cfg_set = cfg_sub.add_parser("set", help="Set a config value")
    cfg_set.add_argument("key", help="Config key (workspace, ollama_host, theme, default_model)")
    cfg_set.add_argument("value", help="Config value")
    cfg_dl = cfg_sub.add_parser("downloads", help="Show download history")

    p_help = sub.add_parser("help", help="Show this help")

    p_plugins = sub.add_parser("plugins", help="List installed LAC plugins")
    p_plugins.set_defaults(func=cmd_plugins)

    p_unlock = sub.add_parser("unlock", help="Activate LAC Pro with your license key")
    p_unlock.add_argument("key", help="Your LAC Pro license key")
    p_unlock.set_defaults(func=cmd_unlock)

    # --- plugin seam: mount plugin CLI subcommands (never fatal) ---
    from backend import plugins as _plugins
    try:
        _found = _plugins.discover()
    except Exception as e:  # noqa: BLE001 — discovery failure must not kill the CLI
        eprint(f"[plugins] discovery failed: {e}")
        _found = []
    for _p in _found:
        reg = getattr(_p.obj, "register_cli", None)
        if not _p.ok or reg is None:
            continue
        try:
            reg(sub)
        except Exception as e:  # noqa: BLE001
            eprint(f"[plugin:{_p.name}] register_cli failed: {e}")
    return parser


def main():
    # Windows' default console codepage (cp1252) can't encode glyphs like
    # '✓' (success lines) or '█'/'░' (the pull progress bar), crashing
    # commands AFTER their action already succeeded. Force UTF-8 with a
    # lossy fallback rather than hunting down every current and future
    # glyph.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    print_banner()
    parser = build_parser()
    args = parser.parse_args()

    if args.host:
        os.environ["OLLAMA_HOST"] = args.host

    if args.command == "help":
        parser.print_help()
        return

    if args.command in ("chat", "tui") or args.command is None:
        from backend.tui.app import run_tui
        run_tui()
        return

    if hasattr(args, "func"):
        return args.func(args)

    commands = {
        "list": cmd_list,
        "ls": cmd_list,
        "pull": cmd_pull,
        "delete": cmd_delete,
        "rm": cmd_delete,
        "ps": cmd_ps,
        "inspect": cmd_inspect,
        "scan": cmd_scan,
        "recommend": cmd_recommend,
        "rec": cmd_recommend,
        "agent": cmd_agent,
        "browse": cmd_browse,
        "workspace": cmd_workspace,
        "ws": cmd_workspace,
        "config": cmd_config,
        "agents": cmd_agents,
        "providers": cmd_providers,
        "mcp": cmd_mcp,
        "openapi": cmd_openapi,
        "session": cmd_session,
        "update": cmd_update,
    }

    cmd_fn = commands.get(args.command)
    if cmd_fn:
        cmd_fn(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
