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

app = Flask(__name__, static_folder="../frontend", static_url_path="", template_folder="../frontend")

PULL_PROGRESS = {}

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


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
    except urllib.error.URLError:
        return None
    except Exception:
        return None


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/scan")
def api_scan():
    info = detect()
    return jsonify({
        "os": info.os,
        "cpu": info.cpu,
        "cores": info.cpu_cores,
        "ram_gb": info.ram_gb,
        "gpus": [{"name": g.name, "vram_gb": g.vram_gb, "backend": g.backend} for g in info.gpus],
        "total_vram_gb": info.total_vram_gb,
        "is_apple_silicon": info.is_apple_silicon,
        "in_container": info.in_container,
    })


@app.route("/api/recommend")
def api_recommend():
    vram = request.args.get("vram", type=float, default=0)
    use_case = request.args.get("use_case", default="coding")
    top_k = request.args.get("top_k", type=int, default=5)

    info = detect()
    if vram and vram > 0:
        info.total_vram_gb = vram
        for gpu in info.gpus:
            if "radeon" in gpu.name.lower() or "amd" in gpu.name.lower():
                gpu.vram_gb = vram
        if not info.gpus:
            from .cookbook.hardware import GPUInfo
            info.gpus = [GPUInfo(name=f"Manual ({vram} GB)", vram_gb=vram, backend="cuda")]

    recs = recommend(info, use_case=use_case, top_k=top_k)
    return jsonify({
        "vram_gb": info.total_vram_gb,
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
                "scores": {
                    "quality": r.quality_score,
                    "speed": r.speed_score,
                    "fit": r.fit_score,
                    "context": r.context_score,
                },
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
    if resp is None:
        return jsonify({"running": False, "version": None})
    return jsonify({
        "running": True,
        "version": resp.get("version", "unknown"),
    })


@app.route("/api/ollama/models")
def ollama_models():
    resp = _ollama_request("GET", "/api/tags")
    if resp is None:
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


@app.route("/api/ollama/check-install")
def ollama_check_install():
    url = "https://ollama.com/download"
    system = platform.system().lower()
    if system == "windows":
        return jsonify({"installed": False, "download_url": url, "instructions": "Download and run the Ollama installer from ollama.com/download"})
    return jsonify({"installed": False, "download_url": url})


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
        "app_name": "Model Hub",
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

def _fetch_library():
    global LIBRARY_CACHE, LIBRARY_CACHE_TIME
    now = time.time()
    if LIBRARY_CACHE and (now - LIBRARY_CACHE_TIME) < LIBRARY_CACHE_TTL:
        return LIBRARY_CACHE
    cache_path = Path(__file__).parent / "cookbook" / "data" / "library_cache.json"
    if cache_path.exists():
        with open(cache_path) as f:
            data = json.load(f)
            if (now - data.get("fetched", 0)) < 86400:
                LIBRARY_CACHE = data["models"]
                LIBRARY_CACHE_TIME = now
                return LIBRARY_CACHE
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
        LIBRARY_CACHE = models
        LIBRARY_CACHE_TIME = now
        try:
            with open(cache_path, "w") as f:
                json.dump({"fetched": now, "models": models}, f)
        except Exception:
            pass
        return models
    except Exception as e:
        if LIBRARY_CACHE:
            return LIBRARY_CACHE
        return {"error": str(e), "models": []}


@app.route("/api/library/browse")
def api_library_browse():
    q = request.args.get("q", "").strip().lower()
    capability = request.args.get("capability", "").strip().lower()
    sort = request.args.get("sort", "pulls")
    result = _fetch_library()
    if isinstance(result, dict) and "error" in result:
        return jsonify(result)
    models = list(result)
    if q:
        models = [m for m in models if q in m["name"].lower() or q in m["description"].lower()]
    if capability:
        models = [m for m in models if any(capability in c.lower() for c in m.get("capabilities", []))]
    def parse_pulls(p):
        try:
            p = p.replace("M", "e6").replace("B", "e9").replace("K", "e3")
            return float(p)
        except (ValueError, TypeError):
            return 0
    if sort == "name":
        models.sort(key=lambda m: m["name"])
    elif sort == "pulls":
        models.sort(key=lambda m: parse_pulls(m.get("pulls", "0")), reverse=True)
    elif sort == "newest":
        models.sort(key=lambda m: m["name"], reverse=True)
    return jsonify({"total": len(models), "models": models})


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


def run_server(host="127.0.0.1", port=5050, debug=False):
    print(f"  Model Hub running at http://{host}:{port}")
    print(f"  Open your browser to that address.\n")
    app.run(host=host, port=port, debug=debug)
