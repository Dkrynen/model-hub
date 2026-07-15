"""CLI plugin mounting: plugins add subcommands; apt plugins lists them."""
from types import SimpleNamespace

import backend.plugins as plugins_mod
from backend.plugins import LoadedPlugin


PRODUCT_STATE = {
    "schema_version": 1,
    "product": "local_pro",
    "entitlement": {"state": "inactive", "plan": None, "expires_human": None, "checked": None},
    "capabilities": [],
}


def loaded(name, version, obj):
    return LoadedPlugin(name, version, obj, host_api_version=1, product_state=PRODUCT_STATE)


def _fake_discover(monkeypatch, plugins):
    monkeypatch.setattr(plugins_mod, "discover", lambda: plugins)


def test_plugin_subcommand_is_mounted(monkeypatch):
    calls = {}

    def register_cli(sub):
        p = sub.add_parser("prototest", help="plugin-added command")
        p.set_defaults(func=lambda args: calls.setdefault("ran", True))

    plug = SimpleNamespace(name="fake", version="9.9", register_cli=register_cli)
    _fake_discover(monkeypatch, [loaded("fake", "9.9", plug)])

    import cli
    parser = cli.build_parser()
    args = parser.parse_args(["prototest"])
    args.func(args)
    assert calls["ran"] is True


def test_broken_register_cli_does_not_crash(monkeypatch):
    def register_cli(sub):
        raise RuntimeError("plugin exploded")

    plug = SimpleNamespace(name="bad", version="0.0", register_cli=register_cli)
    _fake_discover(monkeypatch, [loaded("bad", "0.0", plug)])

    import cli
    parser = cli.build_parser()  # must not raise
    args = parser.parse_args(["list"])
    assert args is not None


def test_cmd_plugins_lists(monkeypatch, capsys):
    plug = SimpleNamespace(name="fake", version="9.9")
    _fake_discover(monkeypatch, [
        loaded("fake", "9.9", plug),
        LoadedPlugin("broken", "?", None, error="ImportError: nope"),
    ])
    import cli
    cli.cmd_plugins(SimpleNamespace())
    out = capsys.readouterr().out
    assert "fake" in out and "9.9" in out
    assert "broken" in out and "error" in out.lower()


def test_discover_failure_does_not_kill_cli(monkeypatch, capsys):
    """If discovery itself raises, build_parser() must still return a working
    parser (warning on stderr), so every CLI invocation keeps functioning."""
    def boom():
        raise RuntimeError("discovery exploded")

    monkeypatch.setattr(plugins_mod, "discover", boom)

    import cli
    parser = cli.build_parser()  # must not raise
    args = parser.parse_args(["list"])
    assert args is not None
    assert "discovery failed" in capsys.readouterr().err


def test_notify_model_installed_calls_hook(monkeypatch):
    calls = []
    plug = SimpleNamespace(name="fake", version="1.0", on_model_installed=lambda m: calls.append(m))
    _fake_discover(monkeypatch, [loaded("fake", "1.0", plug)])

    import cli
    cli._notify_model_installed("llama3.2:3b")
    assert calls == ["llama3.2:3b"]


def test_notify_model_installed_isolates_raising_hook(monkeypatch, capsys):
    def boom(model_name):
        raise RuntimeError("sweep exploded")

    plug = SimpleNamespace(name="bad", version="0.0", on_model_installed=boom)
    _fake_discover(monkeypatch, [loaded("bad", "0.0", plug)])

    import cli
    cli._notify_model_installed("m:1b")  # must not raise
    assert "on_model_installed failed" in capsys.readouterr().err


def test_notify_model_installed_skips_plugin_without_hook(monkeypatch):
    plug = SimpleNamespace(name="fake", version="1.0")  # no on_model_installed attr
    _fake_discover(monkeypatch, [loaded("fake", "1.0", plug)])

    import cli
    cli._notify_model_installed("m:1b")  # must not raise


def test_cmd_pull_fires_hook_on_success(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    import cli
    calls = []
    monkeypatch.setattr(cli, "ollama_stream",
                         lambda path, body, timeout=3600: iter([{"status": "success", "total": 0}]))
    monkeypatch.setattr(cli, "_notify_model_installed", lambda m: calls.append(m))

    args = SimpleNamespace(model="m:1b")
    cli.cmd_pull(args)
    assert calls == ["m:1b"]
