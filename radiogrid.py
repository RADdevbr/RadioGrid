#!/usr/bin/env python3
"""
RadioGrid — composição automática de painéis 2×2 de imagens de RM/TC.

Servidor HTTP local (stdlib apenas) que monitora pastas, lê o nome do paciente
via OCR, agrupa imagens por paciente e — a cada 4 — gera um painel 2×2 600×600.
Um dashboard web (index.html) exibe o status em tempo real via SSE.

Toda a lógica macOS-específica (OCR, composição, notificação, Finder) é
isolada em macos_bridge.py.

Uso:
    python3 radiogrid.py                 # porta 7842, abre o browser
    python3 radiogrid.py --port 8080     # porta customizada
    python3 radiogrid.py --no-browser    # não abre o browser
"""

import os
import sys
import json
import time
import queue
import shutil
import base64
import argparse
import threading
import webbrowser
from collections import Counter
from datetime import datetime, date
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from macos_bridge import (
    ocr_patient_name,
    compose_panel,
    notify_macos,
    open_in_finder,
    choose_folder,
    choose_files,
    normalize_name,
    is_macos,
)

# ------------------------------------------------------------------
# Caminhos e constantes
# ------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "state.json")
INDEX_PATH = os.path.join(BASE_DIR, "index.html")
IMPORT_DIR = os.path.join(BASE_DIR, "imports")
DEFAULT_PORT = 7842
IMAGE_EXTS = (".png", ".jpg", ".jpeg")
PANEL_THRESHOLD = 4

DEFAULT_CONFIG = {
    "watch_folders": ["~/Desktop", "~/Downloads"],
    "output_folder": "~/Desktop/RadioGrid_Output",
    "poll_interval_seconds": 2,
}


# ==================================================================
# Detecção de conflito de nome (seção 11)
# ==================================================================
def check_name_conflict(queue_images):
    """Dado um grupo de imagens, retorna status de consistência de nome."""
    names = [
        img["name_detected"]
        for img in queue_images
        if img.get("name_detected") and img["name_detected"] != "DESCONHECIDO"
    ]
    if not names:
        return {"conflict": False, "reason": "no_ocr"}

    unique_names = set(names)
    if len(unique_names) == 1:
        return {"conflict": False, "name": names[0]}

    most_common = Counter(names).most_common(1)[0][0]
    return {
        "conflict": True,
        "names_found": list(unique_names),
        "most_common": most_common,
        "message": f'Nomes divergentes detectados: {", ".join(unique_names)}',
    }


# ==================================================================
# FolderWatcher (seção 9)
# ==================================================================
class FolderWatcher:
    def __init__(self, folders, callback, interval=2):
        self.folders = list(folders)
        self.callback = callback
        self.interval = interval
        self.seen = set()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        # Seed inicial: marcar arquivos existentes como já vistos.
        for folder in list(self.folders):
            for f in self._scan(folder):
                self.seen.add(f)

        while not self._stop.is_set():
            with self._lock:
                folders = list(self.folders)
            for folder in folders:
                for f in self._scan(folder):
                    if f not in self.seen:
                        self.seen.add(f)
                        try:
                            self.callback(f)
                        except Exception as e:
                            print(f"[WATCHER] erro ao processar {f}: {e}")
            self._stop.wait(self.interval)

    def _scan(self, folder):
        result = []
        try:
            for entry in os.scandir(os.path.expanduser(folder)):
                if entry.is_file() and entry.name.lower().endswith(IMAGE_EXTS):
                    result.append(entry.path)
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            pass
        return result

    def update_folders(self, new_folders):
        with self._lock:
            self.folders = list(new_folders)

    def update_interval(self, interval):
        self.interval = interval

    def stop(self):
        self._stop.set()


# ==================================================================
# SSE Hub — broadcast de eventos para clientes conectados
# ==================================================================
class SSEHub:
    def __init__(self):
        self._clients = []
        self._lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=100)
        with self._lock:
            self._clients.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    def broadcast(self, event, data):
        payload = (event, data)
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass


# ==================================================================
# Núcleo da aplicação — estado, config e pipeline
# ==================================================================
class RadioGrid:
    def __init__(self):
        self.lock = threading.RLock()
        self.config = self._load_config()
        self.state = self._load_state()
        self.hub = SSEHub()
        self.watcher = None
        self.images_detected_today = self._count_detected_today()

    # ----- Config -----
    def _load_config(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                for k, v in DEFAULT_CONFIG.items():
                    cfg.setdefault(k, v)
                return cfg
            except Exception:
                pass
        cfg = dict(DEFAULT_CONFIG)
        self._save_config(cfg)
        return cfg

    def _save_config(self, cfg=None):
        cfg = cfg if cfg is not None else self.config
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)

    # ----- State -----
    def _load_state(self):
        if os.path.exists(STATE_PATH):
            try:
                with open(STATE_PATH, "r", encoding="utf-8") as f:
                    st = json.load(f)
                st.setdefault("queues", {})
                st.setdefault("panels", [])
                return st
            except Exception:
                pass
        return {"queues": {}, "panels": []}

    def _save_state(self):
        # chamado já sob self.lock
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)
        os.replace(tmp, STATE_PATH)

    def _count_detected_today(self):
        today = date.today().isoformat()
        count = 0
        for q in self.state.get("queues", {}).values():
            for img in q.get("images", []):
                if str(img.get("added_at", "")).startswith(today):
                    count += 1
        return count

    # ----- Helpers de path -----
    def output_dir(self):
        return os.path.expanduser(self.config["output_folder"])

    def watched_roots(self):
        roots = [os.path.expanduser(f) for f in self.config["watch_folders"]]
        roots.append(self.output_dir())
        roots.append(IMPORT_DIR)  # imagens importadas manualmente
        return [os.path.realpath(r) for r in roots]

    def path_allowed(self, path):
        """Anti path-traversal: path precisa estar dentro de uma pasta conhecida."""
        try:
            real = os.path.realpath(path)
        except Exception:
            return False
        for root in self.watched_roots():
            if real == root or real.startswith(root + os.sep):
                return True
        return False

    # ----- Watcher lifecycle -----
    def start_watcher(self):
        os.makedirs(self.output_dir(), exist_ok=True)
        self.watcher = FolderWatcher(
            self.config["watch_folders"],
            self.on_new_image,
            self.config.get("poll_interval_seconds", 2),
        )
        self.watcher.start()
        self.hub.broadcast(
            "watcher_status",
            {"folders": self.config["watch_folders"], "status": "watching"},
        )

    # ----- Pipeline -----
    def on_new_image(self, path, source="watcher"):
        name_raw = ocr_patient_name(path)
        patient = normalize_name(name_raw)
        self._add_image(patient, path, name_raw, source)

    def _add_image(self, patient, path, name_raw, source):
        with self.lock:
            q = self.state["queues"].setdefault(
                patient,
                {"images": [], "panel_count": 0, "count": 0,
                 "last_image_at": None, "notified_at_4": False},
            )
            now = datetime.now().isoformat(timespec="seconds")
            img = {
                "path": path,
                "name_detected": patient,
                "name_raw": name_raw,
                "source": source,
                "added_at": now,
            }
            q["images"].append(img)
            q["count"] = len(q["images"])
            q["last_image_at"] = now
            self.images_detected_today += 1

            conflict = check_name_conflict(q["images"])
            self._save_state()
            queue_size = q["count"]
            reached = queue_size >= PANEL_THRESHOLD and not q["notified_at_4"]
            if reached:
                q["notified_at_4"] = True

        self.hub.broadcast(
            "image_detected",
            {"patient": patient, "path": path, "queue_size": queue_size,
             "images_today": self.images_detected_today},
        )
        if conflict.get("conflict"):
            self.hub.broadcast(
                "name_conflict",
                {"patient": patient, "path": path, **conflict},
            )

        if reached:
            self._on_queue_complete(patient)

    def _on_queue_complete(self, patient):
        with self.lock:
            q = self.state["queues"].get(patient)
            if not q:
                return
            panel_number = q["panel_count"] + 1

        self.hub.broadcast(
            "queue_complete",
            {"patient": patient, "count": PANEL_THRESHOLD,
             "panel_number": panel_number,
             "timestamp": datetime.now().isoformat(timespec="seconds")},
        )
        notify_macos(
            title="RadioGrid",
            subtitle="Painel pronto",
            message=f"{patient} — 4 imagens. Gerando painel #{panel_number}...",
        )
        self.generate_panel(patient)

    def generate_panel(self, patient):
        """Gera o painel para o paciente com as imagens atuais na fila."""
        with self.lock:
            q = self.state["queues"].get(patient)
            if not q or not q["images"]:
                return None
            images = list(q["images"])
            panel_number = q["panel_count"] + 1

        paths = [img["path"] for img in images][:PANEL_THRESHOLD]
        safe_name = "_".join(patient.split())
        output_path = os.path.join(
            self.output_dir(), f"{safe_name}_panel_{panel_number}.png"
        )
        os.makedirs(self.output_dir(), exist_ok=True)
        success = compose_panel(paths, output_path)
        if not success:
            print(f"[PANEL] falha ao gerar painel para {patient}")
            return None

        created_at = datetime.now().isoformat(timespec="seconds")
        with self.lock:
            q = self.state["queues"].get(patient, {})
            panel = {
                "path": output_path,
                "patient": patient,
                "created_at": created_at,
                "sources": paths,
                "panel_number": panel_number,
            }
            self.state["panels"].append(panel)
            # Zerar a fila para o paciente.
            q["panel_count"] = panel_number
            q["images"] = []
            q["count"] = 0
            q["notified_at_4"] = False
            self._save_state()

        self.hub.broadcast(
            "panel_generated",
            {"patient": patient, "panel_path": output_path,
             "panel_number": panel_number, "created_at": created_at},
        )
        return panel

    # ----- Ações da API -----
    def update_config(self, new_cfg):
        with self.lock:
            for k in ("watch_folders", "output_folder", "poll_interval_seconds"):
                if k in new_cfg:
                    self.config[k] = new_cfg[k]
            self._save_config()
        if self.watcher:
            self.watcher.update_folders(self.config["watch_folders"])
            self.watcher.update_interval(self.config.get("poll_interval_seconds", 2))
        os.makedirs(self.output_dir(), exist_ok=True)
        self.hub.broadcast(
            "watcher_status",
            {"folders": self.config["watch_folders"], "status": "watching"},
        )
        return self.config

    def queue_clear(self, patient):
        with self.lock:
            if patient in self.state["queues"]:
                self.state["queues"][patient]["images"] = []
                self.state["queues"][patient]["count"] = 0
                self.state["queues"][patient]["notified_at_4"] = False
                self._save_state()

    def queue_remove_image(self, patient, path):
        with self.lock:
            q = self.state["queues"].get(patient)
            if not q:
                return
            q["images"] = [i for i in q["images"] if i["path"] != path]
            q["count"] = len(q["images"])
            if q["count"] < PANEL_THRESHOLD:
                q["notified_at_4"] = False
            self._save_state()
        self.hub.broadcast(
            "image_detected",
            {"patient": patient, "path": path, "queue_size": q["count"],
             "images_today": self.images_detected_today},
        )

    def import_images(self, patient, paths=None, files=None):
        """Importa imagens manualmente para a fila de um paciente.

        Cada imagem é salva em IMPORT_DIR (para thumbnails/Finder funcionarem) e
        adicionada à fila com o paciente informado pelo usuário — sem OCR.

        - paths: lista de caminhos locais (vindos do seletor nativo do macOS).
        - files: lista de {"name": str, "data_base64": str} (upload do navegador).

        Retorna {"ok": bool, "imported": int, "errors": [str]}.
        """
        patient = normalize_name(patient or "")
        if not patient or patient == "DESCONHECIDO":
            return {"ok": False, "imported": 0, "errors": ["Informe o nome do paciente"]}

        os.makedirs(IMPORT_DIR, exist_ok=True)
        imported = 0
        errors = []

        def _safe_name(name):
            base = os.path.basename(name or "imagem.png")
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            return f"{stamp}_{base}"

        for src in (paths or []):
            src = os.path.expanduser(src)
            ext = os.path.splitext(src)[1].lower()
            if ext not in IMAGE_EXTS:
                errors.append(f"Ignorado (não é imagem): {os.path.basename(src)}")
                continue
            if not os.path.isfile(src):
                errors.append(f"Não encontrado: {os.path.basename(src)}")
                continue
            try:
                dest = os.path.join(IMPORT_DIR, _safe_name(src))
                shutil.copy(src, dest)
                self._add_image(patient, dest, patient, "import")
                imported += 1
            except Exception as e:
                errors.append(f"Falha ao importar {os.path.basename(src)}: {e}")

        for item in (files or []):
            name = item.get("name", "imagem.png")
            ext = os.path.splitext(name)[1].lower()
            if ext not in IMAGE_EXTS:
                errors.append(f"Ignorado (não é imagem): {name}")
                continue
            data = item.get("data_base64", "")
            # Aceita data URIs ("data:image/png;base64,....") e base64 puro.
            if "," in data:
                data = data.split(",", 1)[1]
            try:
                raw = base64.b64decode(data)
                dest = os.path.join(IMPORT_DIR, _safe_name(name))
                with open(dest, "wb") as f:
                    f.write(raw)
                self._add_image(patient, dest, patient, "import")
                imported += 1
            except Exception as e:
                errors.append(f"Falha ao importar {name}: {e}")

        return {"ok": imported > 0, "imported": imported, "errors": errors}

    def snapshot(self):
        with self.lock:
            return {
                "queues": json.loads(json.dumps(self.state["queues"])),
                "panels": json.loads(json.dumps(self.state["panels"])),
                "config": dict(self.config),
                "stats": {
                    "active_queues": sum(
                        1 for q in self.state["queues"].values() if q["count"] > 0
                    ),
                    "panels_today": sum(
                        1 for p in self.state["panels"]
                        if str(p.get("created_at", "")).startswith(date.today().isoformat())
                    ),
                    "images_today": self.images_detected_today,
                },
                "is_macos": is_macos(),
                "server_time": datetime.now().isoformat(timespec="seconds"),
            }


APP = None  # instância global, definida em main()


# ==================================================================
# HTTP Handler
# ==================================================================
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # silenciar logs ruidosos do http.server

    # ---------- helpers ----------
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # ---------- GET ----------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/":
            return self._serve_index()
        if path == "/api/state":
            return self._send_json(APP.snapshot())
        if path == "/events":
            return self._serve_sse()
        if path == "/api/image":
            return self._serve_image(qs.get("path", [None])[0])
        if path.startswith("/output/"):
            return self._serve_output(path[len("/output/"):])
        if path == "/api/open-finder":
            return self._open_finder(qs.get("path", [None])[0])
        if path == "/api/choose-folder":
            return self._choose_folder()
        if path == "/api/choose-files":
            return self._choose_files()

        self._send_json({"error": "not found"}, 404)

    # ---------- POST ----------
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_json_body()

        if path == "/api/config":
            return self._send_json(APP.update_config(body))
        if path == "/api/queue/add":
            patient = normalize_name(body.get("patient", "DESCONHECIDO"))
            img_path = body.get("path", "")
            if img_path:
                APP._add_image(patient, img_path, patient, "manual")
            return self._send_json({"ok": True})
        if path == "/api/queue/clear":
            APP.queue_clear(normalize_name(body.get("patient", "")))
            return self._send_json({"ok": True})
        if path == "/api/queue/generate":
            panel = APP.generate_panel(normalize_name(body.get("patient", "")))
            return self._send_json({"ok": panel is not None, "panel": panel})
        if path == "/api/queue/remove-image":
            APP.queue_remove_image(
                normalize_name(body.get("patient", "")), body.get("path", "")
            )
            return self._send_json({"ok": True})
        if path == "/api/import":
            result = APP.import_images(
                body.get("patient", ""),
                paths=body.get("paths"),
                files=body.get("files"),
            )
            return self._send_json(result)

        self._send_json({"error": "not found"}, 404)

    # ---------- handlers específicos ----------
    def _serve_index(self):
        try:
            with open(INDEX_PATH, "rb") as f:
                body = f.read()
            self._send_bytes(body, "text/html; charset=utf-8")
        except FileNotFoundError:
            self._send_bytes(b"index.html not found", "text/plain", 500)

    def _serve_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = APP.hub.subscribe()
        try:
            # comentário inicial para abrir o stream
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    event, data = q.get(timeout=15)
                    msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            APP.hub.unsubscribe(q)

    def _guess_content_type(self, path):
        lower = path.lower()
        if lower.endswith(".png"):
            return "image/png"
        if lower.endswith((".jpg", ".jpeg")):
            return "image/jpeg"
        return "application/octet-stream"

    def _serve_image(self, path):
        if not path:
            return self._send_json({"error": "missing path"}, 400)
        path = os.path.expanduser(path)
        if not APP.path_allowed(path) or not os.path.isfile(path):
            return self._send_json({"error": "forbidden"}, 403)
        try:
            with open(path, "rb") as f:
                data = f.read()
            b64 = base64.b64encode(data).decode("ascii")
            ct = self._guess_content_type(path)
            return self._send_json({"data_uri": f"data:{ct};base64,{b64}"})
        except Exception:
            return self._send_json({"error": "read failed"}, 500)

    def _serve_output(self, filename):
        filename = os.path.basename(filename)  # impede traversal
        full = os.path.join(APP.output_dir(), filename)
        if not os.path.isfile(full):
            return self._send_json({"error": "not found"}, 404)
        try:
            with open(full, "rb") as f:
                body = f.read()
            self._send_bytes(body, self._guess_content_type(full))
        except Exception:
            self._send_json({"error": "read failed"}, 500)

    def _open_finder(self, path):
        if not path:
            return self._send_json({"error": "missing path"}, 400)
        path = os.path.expanduser(path)
        if not APP.path_allowed(path):
            return self._send_json({"error": "forbidden"}, 403)
        open_in_finder(path)
        return self._send_json({"ok": True})

    def _choose_folder(self):
        folder = choose_folder()
        if not folder:
            return self._send_json({"ok": False})
        return self._send_json({"ok": True, "path": folder})

    def _choose_files(self):
        paths = choose_files()
        return self._send_json({"ok": bool(paths), "paths": paths})


# ==================================================================
# main
# ==================================================================
def main():
    global APP
    parser = argparse.ArgumentParser(description="RadioGrid")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    APP = RadioGrid()
    APP.start_watcher()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    url = f"http://localhost:{args.port}"

    print(f"RadioGrid rodando em {url}")
    print(f"  Plataforma macOS: {is_macos()}")
    print(f"  Monitorando: {', '.join(APP.config['watch_folders'])}")
    print(f"  Output: {APP.output_dir()}")
    print("  Ctrl+C para encerrar.")

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando RadioGrid...")
    finally:
        if APP.watcher:
            APP.watcher.stop()
        server.shutdown()


if __name__ == "__main__":
    main()
