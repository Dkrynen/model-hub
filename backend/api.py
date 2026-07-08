import json
import os
import platform
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from flask import Flask, Response, jsonify, request, stream_with_context

from . import self_invoke
from .cookbook import proc
from .cookbook.hardware import detect, print_system
from .cookbook.recommend import recommend, load_models
from .pro_install import install_pro_plugin

try:
    from .version import __version__ as APP_VERSION, __github_url__, __download_url__
except ImportError:
    APP_VERSION = "0.0.0"
    __github_url__ = "https://github.com/Dkrynen/lac"
    __download_url__ = "https://github.com/Dkrynen/lac/releases"

# Serve the built web app (web/dist) when present, else the legacy frontend/.
_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
_STATIC = str(_DIST) if (_DIST / "index.html").exists() else str(_FRONTEND)
app = Flask(__name__, static_folder=_STATIC, static_url_path="", template_folder=_STATIC)

PULL_PROGRESS = {}

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
INTERACTIVE_CONTEXT_FALLBACK = 4096

MODEL_WEIGHT_EXTS = {".gguf", ".safetensors", ".bin", ".onnx", ".pt", ".pth"}
HF_DETAIL_CACHE_TTL_S = 10 * 60
HF_DETAIL_CACHE_MAX = 256
_HF_DETAIL_CACHE: dict[str, tuple[float, dict | None]] = {}
_HF_DETAIL_CACHE_LOCK = threading.Lock()


def _safe_dir_size(path: Path) -> int | None:
    if not path.exists():
        return 0
    try:
        total = 0
        for child in path.rglob("*"):
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    continue
        return total
    except OSError:
        return None


def _default_ollama_models_dir() -> Path:
    configured = os.environ.get("OLLAMA_MODELS")
    if configured:
        return Path(configured).expanduser()
    if platform.system().lower() == "linux":
        return Path("/usr/share/ollama/.ollama/models")
    return Path.home() / ".ollama" / "models"


def _app_payload_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _find_model_weight_files(path: Path, limit: int = 10) -> list[dict]:
    if not getattr(sys, "frozen", False) or not path.exists():
        return []
    found = []
    try:
        for child in path.rglob("*"):
            if child.is_file() and child.suffix.lower() in MODEL_WEIGHT_EXTS:
                found.append({
                    "path": str(child.relative_to(path)),
                    "size_bytes": child.stat().st_size,
                })
                if len(found) >= limit:
                    break
    except OSError:
        return found
    return found


def _serialize_split_plan(plan) -> dict:
    """Serialize a SplitPlan dataclass to a JSON-safe dict for the API."""
    return {
        "run_mode": plan.run_mode,
        "summary": plan.summary,
        "total_model_gb": plan.total_model_gb,
        "total_layers": plan.total_layers,
        "gpu_layers": plan.gpu_layers,
        "env_vars": plan.env_vars,
        "tiers": [
            {
                "kind": a.kind, "name": a.name, "memory_gb": a.memory_gb,
                "allocated_gb": a.allocated_gb, "backend": a.backend,
                "device_index": a.device_index, "bandwidth": a.bandwidth,
                "layers": a.layers,
            }
            for a in plan.tiers
        ],
    }


def _ollama_request(method: str, path: str, json_body: Optional[dict] = None, stream: bool = False):
    import urllib.request
    import urllib.error
    url = f"{OLLAMA_HOST}{path}"
    data = json.dumps(json_body).encode() if json_body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        if stream:
            return resp
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return {"error": f"Ollama HTTP {e.code}: {e.read().decode()[:200]}"}
        except Exception:
            return {"error": f"Ollama HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"error": f"Cannot connect to Ollama at {OLLAMA_HOST}: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def _interactive_context() -> int:
    """Context used by interactive warm/chat paths.

    The recommendation and calibration stack assumes a 4k default context.
    Large Ollama models can advertise 128k+ contexts, which is great when a
    user asks for it, but painful as an implicit chat/warm default.
    """
    try:
        from .config import resolve_config
        ctx = resolve_config().default_context
    except Exception:  # noqa: BLE001
        try:
            from .cookbook.config import load_config
            ctx = load_config().default_context
        except Exception:  # noqa: BLE001
            ctx = INTERACTIVE_CONTEXT_FALLBACK
    try:
        ctx = int(ctx)
    except (TypeError, ValueError):
        return INTERACTIVE_CONTEXT_FALLBACK
    return ctx if ctx > 0 else INTERACTIVE_CONTEXT_FALLBACK

@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/docs")
@app.route("/docs/api")
@app.route("/docs/guide")
def docs_page():
    # Docs is a client-side route in the new web app.
    return app.send_static_file("index.html")


@app.route("/api/openapi.json")
def openapi_spec():
    from .openapi_gen import generate_openapi

    return jsonify(generate_openapi(app, f"http://127.0.0.1:5050"))


@app.route("/api/scan")
def api_scan():
    info = detect()
    return jsonify({
        "os": info.os,
        "cpu": info.cpu,
        "cores": info.cpu_cores,
        "ram_gb": info.ram_gb,
        "gpus": [{"name": g.name, "vram_gb": g.vram_gb, "backend": g.backend,
                  "tier": g.tier, "device_index": g.device_index} for g in info.gpus],
        "total_vram_gb": info.total_vram_gb,
        "combined_vram_gb": info.combined_vram_gb,
        "compute_tiers": [
            {"name": t.name, "memory_gb": t.memory_gb, "backend": t.backend,
             "kind": t.kind, "device_index": t.device_index}
            for t in info.compute_tiers
        ],
        "is_apple_silicon": info.is_apple_silicon,
        "in_container": info.in_container,
    })


@app.route("/api/recommend")
def api_recommend():
    vram = request.args.get("vram", type=float, default=0)
    use_case = request.args.get("use_case", default="coding")
    top_k = request.args.get("top_k", type=int, default=5)
    no_calibration = request.args.get("no_calibration", type=int, default=0)
    gpu_mask_raw = request.args.get("gpu_mask", "")
    allow_spill = request.args.get("allow_spill", type=int, default=1)

    info = detect()
    if vram and vram > 0:
        info.total_vram_gb = vram
        for gpu in info.gpus:
            if "radeon" in gpu.name.lower() or "amd" in gpu.name.lower():
                gpu.vram_gb = vram
        if not info.gpus:
            from .cookbook.hardware import GPUInfo
            info.gpus = [GPUInfo(name=f"Manual ({vram} GB)", vram_gb=vram, backend="cuda")]
        # Manual override updates fit-scoring via total_vram_gb/gpus above,
        # but combined_vram_gb is a separate display field detect() already
        # computed pre-override -- keep it in sync or the UI shows a stale
        # number next to the correctly-overridden one.
        info.combined_vram_gb = round(sum(g.vram_gb for g in info.gpus), 1)

    mask = {int(x) for x in gpu_mask_raw.split(",") if x.strip().isdigit()} if gpu_mask_raw else set()
    if mask:
        masked_gpus = [g for g in info.gpus if g.device_index in mask]
        if masked_gpus:  # fail-safe: a mask matching no GPU is ignored, never a zero-GPU result
            info.gpus = masked_gpus
            info.compute_tiers = [t for t in info.compute_tiers if t.kind == "ram" or t.device_index in mask]
            gpu_vrams = [g.vram_gb for g in info.gpus]
            info.total_vram_gb = round(max(gpu_vrams), 1)
            info.combined_vram_gb = round(sum(gpu_vrams), 1)

    if not allow_spill:
        info.compute_tiers = [t for t in info.compute_tiers if t.kind != "ram"]
        info.ram_gb = 0.0

    # Build the per-machine calibration from benchmarked results (mirrors cli.cmd_recommend).
    if no_calibration:
        _cal = None
    else:
        from .cookbook.calibration import load_calibration, detect_stack
        _stack = detect_stack(info=info)
        _results = str(Path.home() / ".model-hub" / "benchmarks" / "results.jsonl")
        _cal = load_calibration(info, _stack, _results)

    recs = recommend(info, use_case=use_case, top_k=top_k, calibration=_cal)
    return jsonify({
        "vram_gb": info.total_vram_gb,
        "combined_vram_gb": info.combined_vram_gb,
        "ram_gb": info.ram_gb,
        "recommendations": [
            {
                "name": r.model.name,
                "model_id": r.model.id,
                "provider": r.model.provider,
                "params_b": r.model.params_b,
                "quant": r.quant,
                "score": r.score,
                "vram_gb": r.vram_gb,
                "context": r.context_used,
                "run_mode": r.run_mode,
                "ollama_cmd": r.ollama_cmd,
                "speed_source": r.speed_source,
                "speed_band_pct": r.speed_band_pct,
                "scores": {
                    "quality": r.quality_score,
                    "speed": r.speed_score,
                    "fit": r.fit_score,
                    "context": r.context_score,
                },
                "split_plan": _serialize_split_plan(r.split_plan) if r.split_plan else None,
            }
            for r in recs
        ],
    })


@app.route("/api/models")
def api_models():
    all_models = load_models()
    return jsonify([
        {
            "id": m.id,
            "name": m.name,
            "provider": m.provider,
            "params_b": m.params_b,
            "arch": m.arch,
            "context": m.context,
            "use_cases": m.use_cases,
            "is_moe": m.is_moe,
            "vram_q4": m.vram_q4,
            "vram_q8": m.vram_q8,
            "vram_f16": m.vram_f16,
        }
        for m in all_models
    ])


@app.route("/api/ollama/status")
def ollama_status():
    resp = _ollama_request("GET", "/api/version")
    if resp is None or (isinstance(resp, dict) and "error" in resp):
        return jsonify({"running": False, "version": None, "error": resp.get("error") if isinstance(resp, dict) else None})
    return jsonify({
        "running": True,
        "version": resp.get("version", "unknown"),
    })


@app.route("/api/ollama/models")
def ollama_models():
    resp = _ollama_request("GET", "/api/tags")
    if resp is None or (isinstance(resp, dict) and "error" in resp):
        return jsonify([])
    models = []
    for m in resp.get("models", []):
        digest = m.get("digest", "")
        models.append({
            "name": m.get("name"),
            "size_gb": round(m.get("size", 0) / (1024**3), 2),
            "modified": m.get("modified_at", ""),
            "digest_short": digest[:12] if digest else "",
        })
    return jsonify(sorted(models, key=lambda x: x["name"]))


@app.route("/api/ollama/pull", methods=["POST"])
def ollama_pull():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    model_name = data.get("model", "")
    if not model_name:
        return jsonify({"error": "No model specified"}), 400

    def generate():
        import urllib.request
        import urllib.error
        from .cookbook.downloads import log_download
        url = f"{OLLAMA_HOST}/api/pull"
        body = json.dumps({"name": model_name}).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        last_total = 0
        try:
            resp = urllib.request.urlopen(req, timeout=3600)
            for line in resp:
                decoded = line.decode().strip()
                if decoded:
                    yield f"data: {decoded}\n\n"
                    try:
                        chunk = json.loads(decoded)
                    except json.JSONDecodeError:
                        chunk = {}
                    if chunk.get("total"):
                        last_total = chunk["total"]
                    if chunk.get("status") == "success":
                        size_gb = round(last_total / (1024**3), 2) if last_total else 0
                        log_download(model_name, "completed", size_gb)
                        _notify_model_installed_async(model_name)
        except urllib.error.HTTPError as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/ollama/delete", methods=["POST"])
def ollama_delete():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    model_name = data.get("model", "")
    if not model_name:
        return jsonify({"error": "No model specified"}), 400

    result = _ollama_request("DELETE", f"/api/delete", {"name": model_name})
    if isinstance(result, dict) and "error" in result:
        return jsonify({"error": result["error"]}), 500
    return jsonify({"success": True})


def _warm_ollama(model: str) -> dict:
    """Load `model` into VRAM (no generation) and keep it resident. Never raises."""
    import urllib.request
    try:
        body = json.dumps({
            "model": model,
            "prompt": "",
            "stream": False,
            "keep_alive": "30m",
            "options": {"num_ctx": _interactive_context()},
        }).encode()
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/generate", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        raw = urllib.request.urlopen(req, timeout=600).read()
        try:
            data = json.loads(raw.decode() or "{}")
        except Exception:  # noqa: BLE001 - warming should not fail on a bad metrics body
            data = {}
        return {
            "state": "warm",
            "model": model,
            "load_ms": round((data.get("load_duration") or 0) / 1e6, 1),
            "total_ms": round((data.get("total_duration") or 0) / 1e6, 1),
        }
    except Exception as exc:  # noqa: BLE001 - caller decides whether to surface this
        return {"state": "failed", "model": model, "error": str(exc)}


@app.route("/api/ollama/warm", methods=["POST"])
def ollama_warm():
    """Preload a model into VRAM off the chat critical path so the first message
    doesn't pay the cold-load penalty. By default this is fire-and-forget; pass
    {"wait": true} when the UI needs to block sending until the model is loaded."""
    data = request.get_json(silent=True)
    model = data.get("model") if isinstance(data, dict) else None
    if not isinstance(model, str) or not model.strip():
        return jsonify({"error": "model required"}), 400
    wait = bool(data.get("wait")) if isinstance(data, dict) else False
    if wait:
        return jsonify(_warm_ollama(model.strip())), 200
    threading.Thread(target=_warm_ollama, args=(model.strip(),), daemon=True).start()
    return jsonify({"accepted": True}), 200


@app.route("/api/ollama/chat", methods=["POST"])
def ollama_chat():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    model = data.get("model", "")
    messages = data.get("messages", [])
    if not model or not messages:
        return jsonify({"error": "Model and messages required"}), 400

    def generate():
        import urllib.request
        import urllib.error
        url = f"{OLLAMA_HOST}/api/chat"
        body = json.dumps({
            "model": model,
            "messages": messages,
            "stream": True,
            "keep_alive": "30m",
            "options": {"num_ctx": _interactive_context()},
        }).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            resp = urllib.request.urlopen(req, timeout=300)
            for line in resp:
                decoded = line.decode().strip()
                if decoded:
                    yield f"data: {decoded}\n\n"
        except urllib.error.HTTPError as e:
            yield f"data: {json.dumps({'error': f'HTTP {e.code}: {e.reason}'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/ollama/check-install")
def ollama_check_install():
    import shutil
    path = shutil.which("ollama")
    if path:
        return jsonify({"installed": True, "path": path})
    url = "https://ollama.com/download"
    system = platform.system().lower()
    return jsonify({"installed": False, "download_url": url, "instructions": f"Download Ollama from {url}"})


@app.route("/api/system/ollama-path")
def ollama_path():
    import shutil
    path = shutil.which("ollama")
    return jsonify({"path": path})


@app.route("/api/system/version")
def api_version():
    return jsonify({
        "version": APP_VERSION,
        "github_url": __github_url__,
        "download_url": __download_url__,
        "app_name": "LAC",
    })


@app.route("/api/system/storage")
def api_storage():
    app_dir = _app_payload_dir()
    models_dir = _default_ollama_models_dir()
    app_size = _safe_dir_size(app_dir) if getattr(sys, "frozen", False) else None
    model_files = _find_model_weight_files(app_dir)
    return jsonify({
        "app_dir": str(app_dir),
        "app_size_bytes": app_size,
        "ollama_models_dir": str(models_dir),
        "ollama_models_size_bytes": _safe_dir_size(models_dir),
        "ollama_models_configured": bool(os.environ.get("OLLAMA_MODELS")),
        "model_weight_files_in_app": model_files,
        "models_are_bundled": bool(model_files),
        "model_install_mode": "on_demand_ollama_pull",
    })


@app.route("/api/system/check-update")
def api_check_update():
    current = request.args.get("current", APP_VERSION)
    try:
        import urllib.request
        import urllib.error
        import json as _json
        url = "https://api.github.com/repos/Dkrynen/lac/releases/latest"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", f"LAC/{APP_VERSION}")
        resp = urllib.request.urlopen(req, timeout=5)
        data = _json.loads(resp.read().decode())
        latest = data.get("tag_name", "").lstrip("v")
        if latest and latest != current:
            return jsonify({
                "update_available": True,
                "latest_version": latest,
                "download_url": data.get("html_url", ""),
                "release_notes": (data.get("body") or "")[:500],
            })
        return jsonify({"update_available": False, "latest_version": latest, "current_version": current})
    except Exception as e:
        return jsonify({"update_available": False, "error": str(e)})


@app.route("/api/ollama/check-install-detailed")
def ollama_check_detailed():
    import shutil
    path = shutil.which("ollama")
    if path:
        try:
            r = proc.run([path, "--version"], capture_output=True, text=True, timeout=5)
            version = r.stdout.strip() or r.stderr.strip() or "unknown"
        except Exception:
            version = "unknown"
        return jsonify({"installed": True, "path": path, "version": version})

    system = platform.system().lower()
    urls = {
        "windows": "https://ollama.com/download/windows",
        "darwin": "https://ollama.com/download/mac",
        "linux": "https://ollama.com/download/linux",
    }
    return jsonify({
        "installed": False,
        "download_url": urls.get(system, "https://ollama.com/download"),
        "instructions": f"Download and install Ollama from ollama.com/download for your OS.",
    })


LIBRARY_CACHE = None
LIBRARY_CACHE_TIME = 0
LIBRARY_CACHE_TTL = 3600
LIBRARY_CACHE_REFRESHING = False


def _scrape_library():
    """Scrape the Ollama library index. Returns a list of model dicts or
    {"error": str, "models": []} on failure."""
    try:
        import urllib.request
        req = urllib.request.Request("https://ollama.com/library", headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode()
        models = []
        cards = re.split(r'(?=<a\s+href="/library/[^"]+"\s+class="group\s+w-full\s+space-y-5")', html)[1:]
        for card in cards:
            name_m = re.search(r'href="/library/([^"]+)"', card)
            if not name_m:
                continue
            name = name_m.group(1)
            desc_m = re.search(r'class="max-w-lg\s+break-words\s+text-neutral-800\s+text-md">(.*?)</p>', card, re.DOTALL)
            desc = re.sub(r'<[^>]+>', '', desc_m.group(1)).strip() if desc_m else ""
            capabilities = re.findall(r'x-test-capability[^>]*>\s*([^<]+)\s*<', card)
            sizes = re.findall(r'x-test-size[^>]*>\s*([^<]+)\s*<', card)
            pulls_m = re.search(r'x-test-pull-count>([^<]+)<', card)
            pulls = pulls_m.group(1).strip() if pulls_m else "0"
            tags_m = re.search(r'x-test-tag-count>([^<]+)<', card)
            tag_count = tags_m.group(1).strip() if tags_m else "0"
            models.append({
                "name": name,
                "description": desc[:300],
                "capabilities": capabilities,
                "sizes": sizes,
                "pulls": pulls,
                "tag_count": tag_count,
            })
        cache_path = Path(__file__).parent / "cookbook" / "data" / "library_cache.json"
        try:
            with open(cache_path, "w") as f:
                json.dump({"fetched": time.time(), "models": models}, f)
        except Exception:
            pass
        return models
    except Exception as e:
        return {"error": str(e), "models": []}


def _refresh_library_background():
    """Refresh the library cache off the request thread (stale-while-revalidate)."""
    global LIBRARY_CACHE_REFRESHING
    if LIBRARY_CACHE_REFRESHING:
        return
    LIBRARY_CACHE_REFRESHING = True

    def worker():
        global LIBRARY_CACHE, LIBRARY_CACHE_TIME, LIBRARY_CACHE_REFRESHING
        try:
            models = _scrape_library()
            if isinstance(models, list):
                LIBRARY_CACHE = models
                LIBRARY_CACHE_TIME = time.time()
        finally:
            LIBRARY_CACHE_REFRESHING = False

    threading.Thread(target=worker, daemon=True).start()


def _fetch_library():
    """Stale-while-revalidate: serve any cached data instantly and refresh in
    the background when stale. Only the very first (cold) call blocks on a
    live scrape — thereafter Browse loads instantly."""
    global LIBRARY_CACHE, LIBRARY_CACHE_TIME
    now = time.time()

    if LIBRARY_CACHE:
        if now - LIBRARY_CACHE_TIME > LIBRARY_CACHE_TTL:
            _refresh_library_background()
        return LIBRARY_CACHE

    cache_path = Path(__file__).parent / "cookbook" / "data" / "library_cache.json"
    if cache_path.exists():
        try:
            with open(cache_path) as f:
                data = json.load(f)
            models = data.get("models")
            if models:
                LIBRARY_CACHE = models
                LIBRARY_CACHE_TIME = now
                if now - data.get("fetched", 0) > LIBRARY_CACHE_TTL:
                    _refresh_library_background()
                return LIBRARY_CACHE
        except Exception:
            pass

    # Cold cache — scrape synchronously (happens once, ever).
    models = _scrape_library()
    if isinstance(models, list):
        LIBRARY_CACHE = models
        LIBRARY_CACHE_TIME = now
    return models


@app.route("/api/library/browse")
def api_library_browse():
    q = request.args.get("q", "").strip().lower()
    capability = request.args.get("capability", "").strip().lower()
    sort = request.args.get("sort", "pulls")
    compatible = request.args.get("compatible", "").strip()
    result = _fetch_library()
    if isinstance(result, dict) and "error" in result:
        return jsonify(result)
    models = list(result)

    # Always detect system VRAM so every card can show a real fit verdict.
    system_vram = None
    try:
        info = detect()
        system_vram = info.total_vram_gb or (info.gpus[0].vram_gb if info.gpus else 0)
    except Exception:
        system_vram = None

    # Cross-reference each library family against the curated catalog to
    # populate real VRAM/params and a hardware fit verdict (shared with the
    # CLI's `lac browse`, which uses the exact same enrichment).
    from .cookbook.library import enrich_library_models
    models = enrich_library_models(models, system_vram)
    sv = system_vram or 0

    if q:
        models = [m for m in models if q in m["name"].lower() or q in m.get("display", m["name"]).lower() or q in m.get("description", "").lower()]
    if capability:
        models = [m for m in models if any(capability in c.lower() for c in m.get("capabilities", []))]

    if compatible and compatible != "false" and sv:
        if compatible == "gpu":
            models = [m for m in models if m.get("fit") == "gpu"]
        elif compatible == "cpu":
            models = [m for m in models if m.get("fit") in ("offload", "too_big")]

    def parse_pulls(p):
        try:
            p = p.replace("M", "e6").replace("B", "e9").replace("K", "e3")
            return float(p)
        except (ValueError, TypeError):
            return 0

    def parse_vram(m):
        return m.get("vram_q4", 0) or 0

    if sort == "name":
        models.sort(key=lambda m: m.get("display", m["name"]))
    elif sort == "pulls":
        models.sort(key=lambda m: parse_pulls(m.get("pulls", "0")), reverse=True)
    elif sort == "newest":
        models.sort(key=lambda m: m["name"], reverse=True)
    elif sort == "vram":
        models.sort(key=parse_vram)
    elif sort == "params":
        models.sort(key=lambda m: m.get("params_b", 0), reverse=True)

    return jsonify({"total": len(models), "system_vram": system_vram, "models": models})


@app.route("/api/library/tags")
def api_library_tags():
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify({"error": "No model name"}), 400
    try:
        import urllib.request
        url = f"https://ollama.com/library/{name}/tags"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=10)
        html = resp.read().decode()
        tags = re.findall(r'href="/library/' + re.escape(name) + r':([^"]+)"', html)
        tags = sorted(set(tags))
        return jsonify({"name": name, "tags": tags, "count": len(tags)})
    except Exception as e:
        return jsonify({"error": str(e), "name": name, "tags": []})


_GGUF_QUANT_PAT = re.compile(
    r"(?i)(^|[^A-Za-z0-9])"
    r"(IQ[1-4]_(?:XXS|XS|S|M|L|XL|NL)|"
    r"Q[2-8](?:_K(?:_XS|_S|_M|_L|_XL)?|_[0-8])?|"
    r"F32|BF16|F16|FP16)"
    r"([^A-Za-z0-9]|$)"
)
_GGUF_UNSUPPORTED_IMPORT_VARIANT_PAT = re.compile(
    r"(?i)(^|[^A-Za-z0-9])Q[2-8][_-]0[_-](?:4[_-]4|4[_-]8|8[_-]8)([^A-Za-z0-9]|$)"
)
_GGUF_QUANT_SORT = {
    "IQ1_S": 1,
    "IQ1_M": 2,
    "IQ2_XXS": 3,
    "IQ2_XS": 4,
    "IQ2_S": 5,
    "IQ2_M": 6,
    "Q2_K": 7,
    "Q2_K_S": 8,
    "Q2_K_M": 9,
    "Q2_K_L": 10,
    "IQ3_XXS": 11,
    "IQ3_XS": 12,
    "IQ3_S": 13,
    "IQ3_M": 14,
    "Q3_K_S": 15,
    "Q3_K_M": 16,
    "Q3_K_L": 17,
    "Q3_K_XL": 18,
    "IQ4_XS": 19,
    "IQ4_NL": 20,
    "IQ4_M": 21,
    "Q4_0": 22,
    "Q4_K_S": 23,
    "Q4_K_M": 24,
    "Q5_0": 25,
    "Q5_K_S": 26,
    "Q5_K_M": 27,
    "Q6_K": 28,
    "Q8_0": 29,
    "Q8": 30,
    "F16": 31,
    "BF16": 32,
    "F32": 33,
}
_GGUF_IMPORT_PREFERENCE = [
    "Q4_K_M",
    "Q4_K_S",
    "Q5_K_M",
    "Q5_0",
    "Q6_K",
    "Q8_0",
    "Q8",
    "IQ4_XS",
    "IQ4_NL",
    "Q3_K_M",
    "Q3_K_L",
    "Q3_K_S",
    "IQ3_M",
    "IQ3_XS",
    "Q2_K",
    "IQ2_M",
    "F16",
    "BF16",
    "F32",
]


def _gguf_quant(filename: str) -> str | None:
    match = _GGUF_QUANT_PAT.search(filename or "")
    if not match:
        return None
    quant = match.group(2).upper().replace("-", "_").replace("FP16", "F16")
    return quant


def _gguf_import_block_reason(filename: str) -> str | None:
    if _GGUF_UNSUPPORTED_IMPORT_VARIANT_PAT.search(filename or ""):
        return "CPU-tuned GGUF variant; choose the standard file for this quant."
    return None


def _quant_sort_key(quant: str | None) -> tuple[int, str]:
    q = quant or ""
    return (_GGUF_QUANT_SORT.get(q, 999), q)


def _gguf_quants(filenames: list[str]) -> list[str]:
    quants = {_gguf_quant(name) for name in filenames}
    return sorted((q for q in quants if q), key=_quant_sort_key)


def _bytes_to_gb(size_bytes: int | None) -> float | None:
    if not size_bytes:
        return None
    return round(size_bytes / (1024**3), 2)


def _hf_file_fit(size_bytes: int | None, system_vram: float | None, ram_gb: float | None) -> dict:
    size_gb = _bytes_to_gb(size_bytes)
    if not size_gb:
        return {"fit": "unknown", "vram_gb": None}
    # GGUF runtime memory is more than the file on disk: KV cache, graph buffers,
    # and allocator headroom. Keep this deliberately conservative for previews.
    required_gb = round((size_gb * 1.18) + 0.25, 2)
    if system_vram and required_gb <= system_vram * 0.9:
        return {"fit": "fits", "vram_gb": required_gb}
    if (system_vram and required_gb <= system_vram * 2.0) or (ram_gb and required_gb <= ram_gb * 0.75):
        return {"fit": "offload", "vram_gb": required_gb}
    return {"fit": "too_large", "vram_gb": required_gb}


def _hf_gguf_files(siblings: list[dict], system_vram: float | None, ram_gb: float | None) -> list[dict]:
    files = []
    for sibling in siblings:
        if not isinstance(sibling, dict):
            continue
        filename = sibling.get("rfilename") or sibling.get("filename")
        if not isinstance(filename, str) or not filename.lower().endswith(".gguf"):
            continue
        size = sibling.get("size")
        lfs = sibling.get("lfs")
        if not isinstance(size, int) and isinstance(lfs, dict) and isinstance(lfs.get("size"), int):
            size = lfs["size"]
        if not isinstance(size, int):
            size = None
        quant = _gguf_quant(filename)
        fit = _hf_file_fit(size, system_vram, ram_gb)
        compatibility_note = _gguf_import_block_reason(filename)
        files.append({
            "filename": filename,
            "selection": filename,
            "quant": quant,
            "size_bytes": size,
            "size_gb": _bytes_to_gb(size),
            "fit": fit["fit"],
            "vram_gb": fit["vram_gb"],
            "importable": bool(quant) and compatibility_note is None,
            "compatibility_note": compatibility_note,
        })
    return sorted(files, key=lambda f: (_quant_sort_key(f.get("quant")), f["filename"].lower()))


def _choose_hf_file(files: list[dict]) -> dict | None:
    importable = [f for f in files if f.get("importable")]
    if not importable:
        return files[0] if files else None
    non_blocked = [f for f in importable if f.get("fit") != "too_large"] or importable
    by_quant: dict[str, list[dict]] = {}
    for file in non_blocked:
        by_quant.setdefault(file.get("quant") or "", []).append(file)
    for quant in _GGUF_IMPORT_PREFERENCE:
        if quant in by_quant:
            return min(by_quant[quant], key=lambda f: f.get("size_bytes") or 0)
    return min(non_blocked, key=lambda f: _quant_sort_key(f.get("quant")))


def _hf_license(tags: list[str], card_data: dict | None) -> str | None:
    if isinstance(card_data, dict):
        license_name = card_data.get("license")
        if isinstance(license_name, str) and license_name:
            return license_name
    for tag in tags:
        if tag.startswith("license:"):
            return tag.split(":", 1)[1]
    return None


def _hf_base_model(tags: list[str], card_data: dict | None) -> str | None:
    if isinstance(card_data, dict):
        base_model = card_data.get("base_model")
        if isinstance(base_model, str):
            return base_model
        if isinstance(base_model, list) and base_model and isinstance(base_model[0], str):
            return base_model[0]
    for tag in tags:
        if tag.startswith("base_model:") and not tag.startswith("base_model:quantized:"):
            return tag.split(":", 1)[1]
    return None


def _fetch_hf_model_detail(repo_id: str) -> dict | None:
    import urllib.parse
    import urllib.request

    now = time.time()
    with _HF_DETAIL_CACHE_LOCK:
        cached = _HF_DETAIL_CACHE.get(repo_id)
        if cached and now - cached[0] < HF_DETAIL_CACHE_TTL_S:
            return cached[1]

    encoded = "/".join(urllib.parse.quote(part, safe="") for part in repo_id.split("/"))
    req = urllib.request.Request(
        f"https://huggingface.co/api/models/{encoded}?blobs=true",
        headers={"Accept": "application/json", "User-Agent": f"LAC/{APP_VERSION}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode())
    result = data if isinstance(data, dict) else None
    with _HF_DETAIL_CACHE_LOCK:
        _HF_DETAIL_CACHE[repo_id] = (time.time(), result)
        if len(_HF_DETAIL_CACHE) > HF_DETAIL_CACHE_MAX:
            oldest = min(_HF_DETAIL_CACHE, key=lambda key: _HF_DETAIL_CACHE[key][0])
            _HF_DETAIL_CACHE.pop(oldest, None)
    return result


def _hf_gguf_result(
    item: dict,
    system_vram: float | None,
    ram_gb: float | None,
    detail: dict | None = None,
) -> dict | None:
    repo_id = item.get("id") or item.get("modelId")
    if not isinstance(repo_id, str):
        return None
    tags = [t for t in item.get("tags", []) if isinstance(t, str)]
    siblings = item.get("siblings", [])
    if not isinstance(siblings, list):
        siblings = []

    if detail:
        tags = [t for t in detail.get("tags", tags) if isinstance(t, str)]
        detail_siblings = detail.get("siblings", [])
        if isinstance(detail_siblings, list):
            siblings = detail_siblings

    filenames = [
        s.get("rfilename", "")
        for s in siblings
        if isinstance(s, dict) and isinstance(s.get("rfilename"), str)
    ]
    files = _hf_gguf_files(siblings, system_vram, ram_gb)
    if not files and not any(t.lower() == "gguf" for t in tags):
        return None

    selected = _choose_hf_file(files)
    card_data = detail.get("cardData") if isinstance(detail, dict) else item.get("cardData")
    pipeline_tag = (detail or item).get("pipeline_tag") if isinstance((detail or item), dict) else None
    return {
        "repo_id": repo_id,
        "author": (detail or item).get("author") if isinstance((detail or item), dict) else item.get("author"),
        "downloads": (detail or item).get("downloads") or item.get("downloads") or 0,
        "likes": (detail or item).get("likes") or item.get("likes") or 0,
        "gated": bool((detail or item).get("gated")) if isinstance((detail or item), dict) else bool(item.get("gated")),
        "last_modified": (detail or item).get("lastModified") if isinstance((detail or item), dict) else item.get("lastModified"),
        "tags": tags[:8],
        "license": _hf_license(tags, card_data if isinstance(card_data, dict) else None),
        "base_model": _hf_base_model(tags, card_data if isinstance(card_data, dict) else None),
        "pipeline_tag": pipeline_tag if isinstance(pipeline_tag, str) else None,
        "gguf_files": len(files) or len([f for f in filenames if f.lower().endswith(".gguf")]),
        "quants": _gguf_quants([f["filename"] for f in files] or filenames)[:16],
        "files": files[:18],
        "recommended_quant": selected.get("quant") if selected else None,
        "recommended_file": selected.get("filename") if selected else None,
        "recommended_size_gb": selected.get("size_gb") if selected else None,
        "fit": selected.get("fit") if selected else "unknown",
        "vram_gb": selected.get("vram_gb") if selected else None,
    }


def _search_hf_gguf(query: str, limit: int = 12) -> dict:
    """Search public Hugging Face metadata for GGUF repos.

    This is deliberately open-core safe: it reads public HF model metadata only
    and never imports or calls lac_pro. The Pro plugin still owns importing.
    """
    query = " ".join((query or "").split())
    if not query:
        return {"query": query, "total": 0, "models": []}
    limit = max(1, min(int(limit or 12), 24))
    system_vram = None
    ram_gb = None
    try:
        info = detect()
        system_vram = info.total_vram_gb or (info.gpus[0].vram_gb if info.gpus else 0)
        ram_gb = info.ram_gb
    except Exception:
        pass

    try:
        import urllib.parse
        import urllib.request

        params = urllib.parse.urlencode({
            "search": f"{query} gguf",
            "limit": str(limit),
            "full": "false",
        })
        req = urllib.request.Request(
            f"https://huggingface.co/api/models?{params}",
            headers={"Accept": "application/json", "User-Agent": f"LAC/{APP_VERSION}"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001 - search is optional; Browse must still work
        return {"query": query, "total": 0, "models": [], "error": str(exc)}

    candidates = []
    seen: set[str] = set()
    for item in data if isinstance(data, list) else []:
        repo_id = item.get("id") or item.get("modelId")
        if not isinstance(repo_id, str) or repo_id in seen:
            continue
        seen.add(repo_id)
        candidates.append(item)

    details: dict[str, dict | None] = {}
    if candidates:
        workers = min(8, len(candidates))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_fetch_hf_model_detail, item.get("id") or item.get("modelId")): item
                for item in candidates
                if isinstance(item.get("id") or item.get("modelId"), str)
            }
            for future in as_completed(futures):
                item = futures[future]
                repo_id = item.get("id") or item.get("modelId")
                if not isinstance(repo_id, str):
                    continue
                try:
                    details[repo_id] = future.result()
                except Exception:
                    details[repo_id] = None

    out = []
    for item in candidates:
        repo_id = item.get("id") or item.get("modelId")
        detail = details.get(repo_id) if isinstance(repo_id, str) else None
        mapped = _hf_gguf_result(item, system_vram, ram_gb, detail=detail)
        if mapped:
            out.append(mapped)

    return {"query": query, "total": len(out), "system_vram": system_vram, "ram_gb": ram_gb, "models": out}


@app.route("/api/hf/gguf-search")
def api_hf_gguf_search():
    q = request.args.get("q", "").strip()
    try:
        limit = int(request.args.get("limit", "12"))
    except (TypeError, ValueError):
        limit = 12
    return jsonify(_search_hf_gguf(q, limit=limit))


@app.route("/api/ollama/library")
def ollama_library():
    result = _fetch_library()
    if isinstance(result, dict) and "error" in result:
        return jsonify(result)
    return jsonify(result)


@app.route("/api/ollama/ps")
def ollama_ps():
    resp = _ollama_request("GET", "/api/ps")
    if resp is None:
        return jsonify({"running": False, "models": []})
    models = []
    for m in resp.get("models", []):
        models.append({
            "name": m.get("name"),
            "size_gb": round(m.get("size", 0) / (1024**3), 2),
            "digest_short": (m.get("digest") or "")[:12],
        })
    return jsonify({"running": True, "models": models})


@app.route("/api/config/downloads")
def api_config_downloads():
    from .cookbook.downloads import download_history
    return jsonify(download_history())


@app.route("/api/config", methods=["GET"])
def api_get_config():
    from .cookbook.config import load_config
    cfg = load_config()
    return jsonify({
        "workspace": cfg.workspace,
        "ollama_host": cfg.ollama_host,
        "theme": cfg.theme,
        "default_model": cfg.default_model,
    })


@app.route("/api/config", methods=["PUT"])
def api_save_config():
    from .cookbook.config import load_config, save_config
    data = request.get_json() or {}
    cfg = load_config()
    for k in ("workspace", "ollama_host", "theme", "default_model"):
        if k in data:
            setattr(cfg, k, data[k])
    save_config(cfg)
    return jsonify({"success": True})


@app.route("/api/workspaces", methods=["GET"])
def api_list_workspaces():
    from .cookbook.config import list_workspaces
    ws = list_workspaces()
    return jsonify([{"id": w.id, "name": w.name, "description": w.description} for w in ws])


@app.route("/api/workspaces", methods=["POST"])
def api_create_workspace():
    from .cookbook.config import create_workspace
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Workspace name required"}), 400
    try:
        ws = create_workspace(name, data.get("description", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"id": ws.id, "name": ws.name, "description": ws.description}), 201


@app.route("/api/workspaces/<workspace_id>", methods=["GET"])
def api_get_workspace(workspace_id):
    from .cookbook.config import get_workspace
    ws = get_workspace(workspace_id)
    if not ws:
        return jsonify({"error": "Workspace not found"}), 404
    return jsonify({"id": ws.id, "name": ws.name, "description": ws.description})


@app.route("/api/workspaces/<workspace_id>", methods=["DELETE"])
def api_delete_workspace(workspace_id):
    from .cookbook.config import delete_workspace
    if delete_workspace(workspace_id):
        return jsonify({"success": True})
    return jsonify({"error": "Cannot delete default workspace or workspace not found"}), 400


@app.route("/api/workspaces/<workspace_id>/switch", methods=["POST"])
def api_switch_workspace(workspace_id):
    from .cookbook.config import switch_workspace
    if not switch_workspace(workspace_id):
        return jsonify({"error": "Workspace not found"}), 404
    return jsonify({"success": True, "workspace": workspace_id})


@app.route("/api/sessions", methods=["GET"])
def api_list_sessions():
    from .cookbook.persistence import list_sessions
    ws = request.args.get("workspace", "")
    return jsonify(list_sessions(workspace=ws))


@app.route("/api/sessions", methods=["POST"])
def api_create_session():
    from .cookbook.persistence import create_session
    data = request.get_json() or {}
    sid = create_session(
        name=data.get("name", ""),
        model=data.get("model", ""),
        system_prompt=data.get("system_prompt", ""),
        workspace=data.get("workspace", ""),
    )
    return jsonify({"id": sid}), 201


@app.route("/api/sessions/<session_id>", methods=["GET"])
def api_get_session(session_id):
    from .cookbook.persistence import get_session
    session = get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    return jsonify(session)


@app.route("/api/sessions/<session_id>", methods=["PUT"])
def api_save_session(session_id):
    from .cookbook.persistence import save_session
    data = request.get_json() or {}
    save_session(
        session_id=session_id,
        model=data.get("model", ""),
        messages=data.get("messages", []),
        name=data.get("name", ""),
        workspace=data.get("workspace", ""),
    )
    return jsonify({"success": True})


@app.route("/api/sessions/<session_id>", methods=["DELETE"])
def api_delete_session(session_id):
    from .cookbook.persistence import delete_session
    delete_session(session_id)
    return jsonify({"success": True})


@app.route("/api/pro/unlock", methods=["POST"])
def api_pro_unlock():
    """Activate LAC Pro from the browser — the twin of `lac unlock <key>`.

    Hand the license key to install_pro_plugin (which fetches the licensed
    plugin from the delivery gate and installs it) and return its honest dict
    verbatim. That helper NEVER raises: a failed install is reported in the
    body as {"state":"failed","error_type","message"} at HTTP 200 — the
    frontend branches on `state`. A 400 is reserved for a malformed request
    body only (missing or non-string key), matching the guard idiom used by
    the other POST routes in this file.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    key = data.get("key")
    if not isinstance(key, str) or not key.strip():
        return jsonify({"error": "License key required"}), 400
    return jsonify(install_pro_plugin(key.strip()))


@app.route("/api/pro/activate", methods=["POST"])
def api_pro_activate():
    """Self-serve Pro: install the plugin, then write the license grant by
    running `lac pro activate` in a throwaway process with the key on STDIN
    (never argv). Honest JSON states; never raises."""
    data = request.get_json(silent=True)
    key = data.get("key") if isinstance(data, dict) else None
    if not isinstance(key, str) or not key.strip():
        return jsonify({"error": "License key required"}), 400
    key = key.strip()

    installed = install_pro_plugin(key)
    if installed.get("state") != "installed":
        return jsonify({"state": "install_failed", **{k: v for k, v in installed.items() if k != "state"}})

    try:
        r = proc.run([*self_invoke.cli_prefix(), "pro", "activate"],
                     input=key + "\n", capture_output=True, text=True, timeout=60)
    except Exception as e:  # noqa: BLE001 — subprocess spawn failure
        return jsonify({"state": "activation_failed",
                        "message": f"Could not run activation: {e}"})
    if r.returncode != 0:
        lines = (r.stdout or r.stderr or "activation failed").strip().splitlines()
        msg = lines[-1].strip() if lines else "activation failed"
        return jsonify({"state": "activation_failed", "message": msg})
    return jsonify({"state": "activated"})


@app.route("/api/app/relaunch", methods=["POST"])
def api_app_relaunch():
    """Self-relaunch the desktop window so a freshly-installed Pro plugin
    mounts on a clean startup. desktop.relaunch() exits this process on
    success (the response never reaches the client); on failure it returns
    False without exiting, so we report a normal JSON body and the user can
    restart LAC manually."""
    from backend import desktop
    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    view = data.get("view")
    bounds = data.get("bounds")
    ok = desktop.relaunch(view=view, bounds=bounds)
    if not ok:
        return jsonify({"state": "failed",
                         "message": "Could not relaunch; please restart LAC manually."})
    return jsonify({"state": "relaunching"})


@app.errorhandler(404)
def spa_fallback(_e):
    # Client-side routes (e.g. /browse, /chat) -> index.html; API 404 -> JSON.
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    index_path = Path(app.static_folder) / "index.html"
    if index_path.exists():
        return app.send_static_file("index.html")
    return (
        "Web app not built. Run `npm run build` inside web/, or `npm run dev` for development.",
        404,
    )


@app.errorhandler(400)
def bad_request_json(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Bad request"}), 400
    return e


@app.errorhandler(405)
def method_not_allowed_json(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Method not allowed"}), 405
    return e


def run_server(host="127.0.0.1", port=5050, debug=False):
    print(f"  LAC running at http://{host}:{port}")
    print(f"  Open your browser to that address.\n")
    # Pre-warm the library cache in the background so Browse loads instantly.
    threading.Thread(target=_fetch_library, daemon=True).start()
    app.run(host=host, port=port, debug=debug)


# --- plugin seam -----------------------------------------------------------

def _discover_plugins_safe():
    """Call plugins.discover(), isolating discovery-itself failures.

    Mirrors the CLI-layer guard in cli.py: a broken discover() (e.g. a
    corrupt entry point) must never break core — warn and act as if no
    plugins are installed.
    """
    from backend import plugins as _plugins
    try:
        return _plugins.discover()
    except Exception as e:  # noqa: BLE001 — discovery failure must not kill the API
        print(f"[plugins] discovery failed: {e}")
        return []


def _notify_model_installed(model_name: str) -> None:
    """Call every plugin's on_model_installed(model_name), isolated per-plugin
    (mirrors _mount_plugins()'s isolation). A missing hook, a plugin that
    isn't installed, or a raising hook must never affect the install that
    already succeeded."""
    for p in _discover_plugins_safe():
        hook = getattr(p.obj, "on_model_installed", None)
        if not p.ok or hook is None:
            continue
        try:
            hook(model_name)
        except Exception as e:  # noqa: BLE001
            print(f"[plugin:{p.name}] on_model_installed failed: {e}")


def _notify_model_installed_async(model_name: str) -> None:
    """Fire _notify_model_installed in a background thread so a slow plugin
    hook (e.g. LAC Pro's benchmark+sweep+apply autopilot) never delays the
    pull's HTTP response. Mirrors _refresh_library_background()'s pattern."""
    threading.Thread(target=_notify_model_installed, args=(model_name,), daemon=True).start()


@app.route("/api/plugins")
def api_plugins():
    return jsonify([
        {"name": p.name, "version": p.version, "ok": p.ok, "error": p.error}
        for p in _discover_plugins_safe()
    ])


def _mount_plugins(flask_app):
    """Call each plugin's register_api(app). Isolated: a broken plugin logs and moves on."""
    for p in _discover_plugins_safe():
        reg = getattr(p.obj, "register_api", None)
        if not p.ok or reg is None:
            continue
        try:
            reg(flask_app)
        except Exception as e:  # noqa: BLE001
            print(f"[plugin:{p.name}] register_api failed: {e}")


_mount_plugins(app)
