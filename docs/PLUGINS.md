# LAC Plugins

LAC discovers plugins through the `lac.plugins` entry-point group.
(The `lac.tools` group is separate — it belongs to the TUI agent-tool
plugin system in `backend/plugin/`.)

## Writing a plugin
`pyproject.toml`:

    [project.entry-points."lac.plugins"]
    myplugin = "my_pkg.plugin:PLUGIN"

`PLUGIN` is any object with:
- `name: str`, `version: str`
- optional `register_cli(subparsers)` — add argparse subcommands (use `set_defaults(func=...)`)
- optional `register_api(app)` — add Flask routes

Errors in a plugin never break LAC: load and registration are isolated per
plugin (`backend/plugins.py`), and a failure in discovery itself degrades to
a warning. Inspect with `lac plugins` or `GET /api/plugins`.

## Product extensions

Generic community plugins keep the optional hook contract above. A plugin that
integrates into LAC's product/account state must opt in with a stable
`product_id`. The Local Pro contract is:

- `product_id = "local_pro"`
- `host_api_version = 1`
- `product_state()` returning the exact bounded Product State v1 envelope

Product extensions with a missing or mismatched contract are reported as
`incompatible` and none of their hooks are mounted. Core discovers this through
the generic entry point and never imports the private package.
