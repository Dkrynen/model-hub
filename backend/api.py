import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from flask import Flask, Response, jsonify, request, stream_with_context

from .cookbook.hardware import detect, print_system
from .cookbook.recommend import recommend, load_models

try:
    from .version import __version__ as APP_VERSION, __github_url__, __download_url__
except ImportError:
    APP_VERSION = "0.0.0"
    __github_url__ = "https://github.com/Dkrynen/model-hub"
    __download_url__ = "https://github.com/Dkrynen/model-hub/releases"

# Serve the built web app (web/dist) when present, else the legacy frontend/.
_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
_STATIC = str(_DIST) if (_DIST / "index.html").exists() else str(_FRONTEND)
app = Flask(__name__, static_folder=_STATIC, static_url_path="", template_folder=_STATIC)

PULL_PROGRESS = {}

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


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
    resp = _ollama_request("GET", "/api/tags")
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
    data = request.get_json()
    model_name = data.get("model", "")
    if not model_name:
        return jsonify({"error": "No model specified"}), 400

    def generate():
        import urllib.request
        import urllib.error
        url = f"{OLLAMA_HOST}/api/pull"
        body = json.dumps({"name": model_name}).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            resp = urllib.request.urlopen(req, timeout=3600)
            for line in resp:
                decoded = line.decode().strip()
                if decoded:
                    yield f"data: {decoded}\n\n"
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
    data = request.get_json()
    model_name = data.get("model", "")
    if not model_name:
        return jsonify({"error": "No model specified"}), 400

    result = _ollama_request("DELETE", f"/api/delete", {"name": model_name})
    if result is None:
        return jsonify({"error": "Failed to delete model"}), 500
    return jsonify({"success": True})


@app.route("/api/ollama/chat", methods=["POST"])
def ollama_chat():
    data = request.get_json()
    model = data.get("model", "")
    messages = data.get("messages", [])
    if not model or not messages:
        return jsonify({"error": "Model and messages required"}), 400

    def generate():
        import urllib.request
        import urllib.error
        url = f"{OLLAMA_HOST}/api/chat"
        body = json.dumps({"model": model, "messages": messages, "stream": True}).encode()
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


@app.route("/api/benchmark", methods=["POST"])
def api_benchmark():
    """Stream a benchmark run: yields one SSE frame per iteration (tok/s etc.),
    then a final {done, median_tps, runs, logged} summary. Each run is stamped
    with the machine fingerprint and logged to results.jsonl so subsequent
    /api/recommend calls pick up the calibration."""
    import statistics
    import urllib.error
    import urllib.request

    data = request.get_json() or {}
    model = (data.get("model") or "").strip()
    if not model:
        return jsonify({"error": "Model required"}), 400

    prompt = data.get("prompt") or "Write a short function in Python that calculates fibonacci numbers."
    num_predict = int(data.get("num_predict", 128))
    temperature = float(data.get("temperature", 0.0))
    repeat = max(1, min(int(data.get("repeat", 1)), 10))
    no_cache = bool(data.get("no_cache", False))

    def generate():
        from .cookbook.benchmark import build_metrics, log_result
        from .cookbook.calibration import detect_stack, machine_fingerprint

        info = detect()
        stack = detect_stack(info=info)
        fp = machine_fingerprint(info, stack)

        options = {"num_predict": num_predict, "temperature": temperature}
        if no_cache:
            options["prompt_cache_disable"] = True
        body = json.dumps({
            "model": model, "prompt": prompt, "stream": False, "options": options,
        }).encode()

        tps_values: list[float] = []
        logged = 0
        for i in range(repeat):
            try:
                req = urllib.request.Request(f"{OLLAMA_HOST}/api/generate", data=body, method="POST")
                req.add_header("Content-Type", "application/json")
                resp = urllib.request.urlopen(req, timeout=300)
                result = json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                yield _sse({"run": i, "error": f"Ollama HTTP {e.code}"})
                yield _sse_done()
                return
            except Exception as e:
                yield _sse({"run": i, "error": str(e)})
                yield _sse_done()
                return

            entry = build_metrics(result, model, prompt, num_predict, temperature,
                                  fingerprint=fp, stack=stack)
            if log_result(entry):
                logged += 1
            tps_values.append(entry["tokens_per_second"])
            yield _sse({
                "run": i,
                "tokens_per_second": entry["tokens_per_second"],
                "eval_count": entry["eval_count"],
                "eval_duration_ms": entry["eval_duration_ms"],
                "time_to_first_token_ms": entry["time_to_first_token_ms"],
                "response_len": len(entry["response"]),
            })

        median = statistics.median(tps_values)
        yield _sse({"done": True, "median_tps": round(median, 2),
                    "runs": tps_values, "logged": logged})
        yield _sse_done()

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _sse_done() -> str:
    return "data: [DONE]\n\n"


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
        "app_name": "Apt",
    })


@app.route("/api/system/check-update")
def api_check_update():
    current = request.args.get("current", APP_VERSION)
    try:
        import urllib.request
        import urllib.error
        import json as _json
        url = "https://api.github.com/repos/Dkrynen/model-hub/releases/latest"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "model-hub/1.0")
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
            r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
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

    # Cross-reference each library family against the curated catalog (the cookbook)
    # to populate real VRAM/params and a hardware fit verdict.
    catalog_by_family: dict[str, list] = {}
    try:
        for cm in load_models():
            catalog_by_family.setdefault(cm.id.split(":")[0], []).append(cm)
    except Exception:
        pass

    sv = system_vram or 0
    for m in models:
        fam = m.get("name", "")
        variants = catalog_by_family.get(fam)
        if variants:
            variants = sorted(variants, key=lambda v: v.vram_q4 or 0)
            fitting = [v for v in variants if (v.vram_q4 or 0) <= sv * 0.9]
            if fitting:
                rep = fitting[-1]
                m["fit"] = "gpu"
            else:
                rep = variants[0]
                m["fit"] = "offload" if (rep.vram_q4 or 0) <= sv * 2 else "too_big"
            m["vram_q4"] = rep.vram_q4
            m["params_b"] = rep.params_b
        elif m.get("sizes"):
            # No catalog match — rough estimate from advertised sizes (e.g. "3B").
            try:
                pb = float(re.sub(r"[^0-9.]", "", str(m["sizes"][0])) or 0)
                if pb:
                    vq4 = round(pb * 0.6, 1)
                    m["params_b"] = pb
                    m["vram_q4"] = vq4
                    if sv:
                        m["fit"] = "gpu" if vq4 <= sv * 0.9 else ("offload" if vq4 <= sv * 2 else "too_big")
                    else:
                        m["fit"] = "unknown"
                else:
                    m["fit"] = "unknown"
            except Exception:
                m["fit"] = "unknown"
        else:
            m["fit"] = "unknown"

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
    log_file = Path.home() / ".model-hub" / "downloads" / "history.jsonl"
    if not log_file.exists():
        return jsonify([])
    entries = []
    try:
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        pass
    return jsonify(entries)


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
    ws = create_workspace(name, data.get("description", ""))
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
    from .cookbook.config import switch_workspace, list_sessions
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


def run_server(host="127.0.0.1", port=5050, debug=False):
    print(f"  Apt running at http://{host}:{port}")
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
