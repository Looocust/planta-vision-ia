"""Levanta el sistema completo con un solo comando, en Windows, macOS o Linux.

    uv run python scripts/start.py              # datos + modelo + servicios
    uv run python scripts/start.py --data-only  # solo descarga y verifica el dataset
    uv run python scripts/start.py --prepare    # datos + modelo, sin levantar servicios

Pasos:
  1. Descarga COIL-100 si falta y verifica su SHA-256.
  2. Entrena el modelo si el artefacto (MODEL_DIR) no está completo.
  3. Arranca la API de visión (8001) y el agente (8002), con logs en data/runtime/logs,
     y espera a que /health devuelva status "ok".
  4. Ctrl+C detiene ambos servicios.

Solo usa la librería estándar, para no depender de bash, curl, unzip ni make.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COIL_URL = "https://www.cs.columbia.edu/CAVE/databases/SLAM_coil-20_coil-100/coil-100/coil-100.zip"
COIL_SHA256 = "fcb4b8b01c0778f416d8d7a09f45082c4f7075cc74743dcddd5c44307bd4afe7"
EXPECTED_IMAGES = 7200
MODEL_REQUIRED_FILES = ("metadata.json", "model.joblib")

# La consola de Windows puede no usar UTF-8: evita que un acento rompa la salida.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def log(msg: str) -> None:
    print(f"[start] {msg}", flush=True)


def die(msg: str) -> None:
    print(f"[start] ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def load_env() -> None:
    """Carga .env sin pisar variables ya definidas en el entorno."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def count_images(img_dir: Path) -> int:
    return len(list(img_dir.glob("obj*__*.png"))) if img_dir.is_dir() else 0


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def step_data(img_dir: Path) -> None:
    raw_dir = img_dir.parent
    raw_dir.mkdir(parents=True, exist_ok=True)
    n = count_images(img_dir)
    if n >= EXPECTED_IMAGES:
        log(f"Dataset presente: {n} imágenes en {img_dir}")
        return
    zip_path = raw_dir / "coil-100.zip"
    if not zip_path.exists():
        log(f"Descargando COIL-100 (~125 MB) desde {COIL_URL} …")
        part = zip_path.with_suffix(".zip.part")
        try:
            urllib.request.urlretrieve(COIL_URL, part)
        except Exception as e:  # red caída, proxy, etc.
            die(f"No se pudo descargar el dataset: {e}")
        part.replace(zip_path)
    log("Verificando SHA-256…")
    got = sha256_of(zip_path)
    if got != COIL_SHA256:
        zip_path.replace(zip_path.with_suffix(".zip.corrupto"))
        die(f"SHA-256 no coincide (esperado {COIL_SHA256}, obtenido {got}). Vuelve a ejecutar para re-descargar.")
    log("Descomprimiendo…")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(raw_dir)
    n = count_images(img_dir)
    if n < EXPECTED_IMAGES:
        die(f"Se esperaban {EXPECTED_IMAGES} imágenes y hay {n} en {img_dir}")
    log(f"Dataset listo: {n} imágenes")


def step_model(model_dir: Path, log_dir: Path) -> None:
    missing = [f for f in MODEL_REQUIRED_FILES if not (model_dir / f).is_file() or (model_dir / f).stat().st_size == 0]
    if not missing:
        log(f"Modelo completo en {model_dir}")
        return
    log(f"Entrenando modelo (faltan {', '.join(missing)}; puede tardar unos minutos)…")
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "train.log").open("w", encoding="utf-8") as out:
        rc = subprocess.call([sys.executable, "scripts/train_vision.py"], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT)
    if rc != 0:
        die(f"El entrenamiento falló; revisa {log_dir / 'train.log'}")


def health(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return json.loads(r.read().decode("utf-8")).get("status")
    except Exception:
        return None


def start_service(name: str, app: str, port: int, log_dir: Path) -> subprocess.Popen:
    if health(f"http://localhost:{port}/health") is not None:
        die(f"El puerto {port} ya está en uso (¿una ejecución anterior sigue abierta?). Ciérrala y vuelve a intentar.")
    log(f"Arrancando {name} en :{port} …")
    out = (log_dir / f"{name}.log").open("w", encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", app, "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, env=os.environ.copy(),
    )


def wait_ready(name: str, proc: subprocess.Popen, port: int, timeout: int) -> None:
    url = f"http://localhost:{port}/health"
    deadline = time.time() + timeout
    status = None
    while time.time() < deadline:
        if proc.poll() is not None:
            die(f"{name} se cerró al arrancar; revisa data/runtime/logs/{name}.log")
        status = health(url)
        if status == "ok":
            log(f"{name} listo → {url}")
            return
        time.sleep(1)
    die(f"{name} no quedó listo tras {timeout} s (último status: {status!r}); revisa data/runtime/logs/{name}.log")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("", "--data-only", "--prepare"):
        die(f"Opción desconocida: {mode}. Usa --data-only o --prepare, o ninguna.")
    os.chdir(ROOT)
    load_env()
    if os.environ.get("ADMIN_TOKEN") and not os.environ.get("VISION_ADMIN_TOKEN"):
        os.environ["VISION_ADMIN_TOKEN"] = os.environ["ADMIN_TOKEN"]
    img_dir = Path(os.environ.setdefault("DATA_DIR", "data/raw/coil-100"))
    model_dir = Path(os.environ.setdefault("MODEL_DIR", "models/vision-1.0.2"))
    vision_port = int(os.environ.get("VISION_PORT", "8001"))
    agent_port = int(os.environ.get("AGENT_PORT", "8002"))
    timeout = int(os.environ.get("HEALTH_TIMEOUT", "300"))
    log_dir = ROOT / "data" / "runtime" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "runtime" / "uploads").mkdir(parents=True, exist_ok=True)

    step_data(img_dir)
    if mode == "--data-only":
        return
    step_model(model_dir, log_dir)
    if mode == "--prepare":
        return

    procs: list[subprocess.Popen] = []
    try:
        vision = start_service("vision", "services.vision_api.app:app", vision_port, log_dir)
        procs.append(vision)
        wait_ready("vision", vision, vision_port, timeout)
        agent = start_service("agent", "services.agent.app:app", agent_port, log_dir)
        procs.append(agent)
        wait_ready("agent", agent, agent_port, timeout)
        print(
            "\n  Sistema arriba\n"
            f"  API de visión  http://localhost:{vision_port}/docs\n"
            f"  Copiloto       http://localhost:{agent_port}/\n"
            "  Logs           data/runtime/logs/\n"
            "  Ctrl+C para detener ambos servicios.\n",
            flush=True,
        )
        while all(p.poll() is None for p in procs):
            time.sleep(1)
        die("Un servicio se detuvo; revisa los logs en data/runtime/logs/")
    except KeyboardInterrupt:
        pass
    finally:
        if procs:
            log("Deteniendo servicios…")
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
