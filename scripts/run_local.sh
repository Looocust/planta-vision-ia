#!/usr/bin/env bash
# Levanta el sistema completo en local con un solo comando (sin Docker).
#
#   scripts/run_local.sh              # sync + datos + modelo + servicios
#   scripts/run_local.sh --data-only  # solo descarga y verifica el dataset
#   scripts/run_local.sh --prepare    # datos + modelo, sin levantar servicios
#
# Pasos:
#   1. uv sync (dependencias bloqueadas por uv.lock)
#   2. Descarga COIL-100 si falta y verifica su SHA-256
#   3. Entrena el modelo si el artefacto models/vision-1.0.2 no está completo
#      (metadata.json + model.joblib)
#   4. Arranca la API de visión (8001) y el agente (8002) en segundo plano,
#      con logs en data/runtime/logs, y espera a que /health devuelva status "ok"
#   5. Al pulsar Ctrl+C detiene ambos procesos (trap)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MODE="all"
case "${1:-}" in
  --data-only) MODE="data" ;;
  --prepare) MODE="prepare" ;;
  "" ) ;;
  -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
  *) echo "Opción desconocida: $1" >&2; exit 2 ;;
esac

# Carga .env si existe (sin pisar variables ya exportadas en la shell).
if [[ -f .env ]]; then
  while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
    key="$(echo "$key" | xargs)"
    value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
    if [[ -z "${!key:-}" ]]; then export "$key=$value"; fi
  done < .env
fi

# El agente llama a los endpoints admin de visión con VISION_ADMIN_TOKEN; por defecto, el mismo ADMIN_TOKEN.
if [[ -n "${ADMIN_TOKEN:-}" && -z "${VISION_ADMIN_TOKEN:-}" ]]; then export VISION_ADMIN_TOKEN="$ADMIN_TOKEN"; fi

# DATA_DIR = carpeta que CONTIENE las imágenes objN__A.png (la API de visión une DATA_DIR + image_id).
DATA_DIR="${DATA_DIR:-data/raw/coil-100}"
# Compatibilidad: un .env antiguo con DATA_DIR=data/raw se corrige a data/raw/coil-100.
if [[ "$(basename "$DATA_DIR")" != "coil-100" ]] && ! ls "$DATA_DIR"/obj1__0.png >/dev/null 2>&1; then
  printf '[run_local] AVISO: DATA_DIR=%s no contiene imágenes; se usa %s/coil-100\n' "$DATA_DIR" "$DATA_DIR" >&2
  DATA_DIR="$DATA_DIR/coil-100"
fi
export DATA_DIR
IMG_DIR="$DATA_DIR"
RAW_DIR="$(dirname "$IMG_DIR")"          # el zip se descarga y descomprime aquí (crea coil-100/)
MODEL_DIR="${MODEL_DIR:-models/vision-1.0.2}"
# Archivos que debe tener un artefacto completo (separados por espacio).
MODEL_REQUIRED_FILES="${MODEL_REQUIRED_FILES:-metadata.json model.joblib}"
VISION_PORT="${VISION_PORT:-8001}"
AGENT_PORT="${AGENT_PORT:-8002}"
LOG_DIR="data/runtime/logs"
COIL_URL="${COIL_URL:-https://www.cs.columbia.edu/CAVE/databases/SLAM_coil-20_coil-100/coil-100/coil-100.zip}"
COIL_SHA256="fcb4b8b01c0778f416d8d7a09f45082c4f7075cc74743dcddd5c44307bd4afe7"
ZIP_PATH="$RAW_DIR/coil-100.zip"
EXPECTED_IMAGES=7200

log() { printf '\033[1;34m[run_local]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[run_local] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}

count_images() { { find "$IMG_DIR" -maxdepth 1 -name 'obj*__*.png' 2>/dev/null || true; } | wc -l | tr -d ' '; }

step_sync() {
  command -v uv >/dev/null 2>&1 || die "uv no está instalado: https://docs.astral.sh/uv/"
  log "Sincronizando dependencias (uv sync --frozen)…"
  uv sync --frozen
}

step_data() {
  mkdir -p "$RAW_DIR"
  local n; n="$(count_images)"
  if [[ "$n" -ge "$EXPECTED_IMAGES" ]]; then
    log "Dataset presente: $n imágenes en $IMG_DIR"
    return
  fi
  if [[ ! -f "$ZIP_PATH" ]]; then
    log "Descargando COIL-100 desde $COIL_URL …"
    curl -fL --retry 3 -o "$ZIP_PATH.part" "$COIL_URL" || die "No se pudo descargar el dataset"
    mv "$ZIP_PATH.part" "$ZIP_PATH"
  fi
  log "Verificando SHA-256…"
  local got; got="$(sha256_of "$ZIP_PATH")"
  if [[ "$got" != "$COIL_SHA256" ]]; then
    mv "$ZIP_PATH" "$ZIP_PATH.corrupto"
    die "SHA-256 no coincide (esperado $COIL_SHA256, obtenido $got). Archivo movido a $ZIP_PATH.corrupto"
  fi
  log "Descomprimiendo…"
  unzip -q -o "$ZIP_PATH" -d "$RAW_DIR"
  n="$(count_images)"
  [[ "$n" -ge "$EXPECTED_IMAGES" ]] || die "Se esperaban $EXPECTED_IMAGES imágenes y hay $n en $IMG_DIR"
  log "Dataset listo: $n imágenes"
}

model_complete() {
  local f
  for f in $MODEL_REQUIRED_FILES; do
    [[ -s "$MODEL_DIR/$f" ]] || { echo "$f"; return 1; }
  done
  return 0
}

step_model() {
  local missing
  if missing="$(model_complete)"; then
    log "Modelo completo en $MODEL_DIR ($MODEL_REQUIRED_FILES)"
    return
  fi
  if [[ -d "$MODEL_DIR" ]]; then
    log "Artefacto incompleto en $MODEL_DIR (falta $missing); se reentrena"
  fi
  [[ -f scripts/train_vision.py ]] || die "Falta scripts/train_vision.py para entrenar el modelo"
  log "Entrenando modelo (puede tardar unos minutos)…"
  mkdir -p "$LOG_DIR"
  uv run python scripts/train_vision.py 2>&1 | tee "$LOG_DIR/train.log"
  missing="$(model_complete)" || die "El entrenamiento terminó pero falta $MODEL_DIR/$missing"
}

PIDS=()
cleanup() {
  if [[ ${#PIDS[@]} -gt 0 ]]; then
    log "Deteniendo servicios…"
    for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
    wait 2>/dev/null || true
    PIDS=()
  fi
}

# Listo = /health responde 200 Y {"status": "ok"}. "degraded" (p. ej. modelo no cargado) NO cuenta como listo.
health_status() {
  curl -fsS --max-time 3 "$1" 2>/dev/null | sed -n 's/.*"status"[[:space:]]*:[[:space:]]*"\([a-z_]*\)".*/\1/p'
}

wait_health() {
  local name="$1" url="$2" timeout="${3:-${HEALTH_TIMEOUT:-300}}" i=0 st=""
  while true; do
    st="$(health_status "$url" || true)"
    [[ "$st" == "ok" ]] && break
    i=$((i + 1))
    if [[ $i -ge $timeout ]]; then
      die "$name no quedó listo en $url tras ${timeout}s (último status: '${st:-sin respuesta}'). Revisa $LOG_DIR/${name}.log"
    fi
    for pid in "${PIDS[@]}"; do
      kill -0 "$pid" 2>/dev/null || die "$name terminó inesperadamente. Revisa $LOG_DIR/${name}.log"
    done
    sleep 1
  done
  log "$name listo (status=ok) → $url"
}

step_services() {
  mkdir -p "$LOG_DIR" data/runtime/uploads
  [[ -f services/vision_api/app.py ]] || die "Falta services/vision_api/app.py"
  [[ -f services/agent/app.py ]] || die "Falta services/agent/app.py"
  # Un proceso previo en el mismo puerto respondería a los health checks en lugar del nuevo.
  for port in "$VISION_PORT" "$AGENT_PORT"; do
    if curl -s -o /dev/null --max-time 2 "http://localhost:$port/health"; then
      die "El puerto $port ya está en uso (¿una ejecución anterior sigue viva?). Deténla: lsof -ti :$port | xargs kill"
    fi
  done
  trap cleanup EXIT
  trap 'exit 130' INT TERM

  log "Arrancando API de visión en :$VISION_PORT …"
  uv run uvicorn services.vision_api.app:app --host 0.0.0.0 --port "$VISION_PORT" \
    >"$LOG_DIR/vision.log" 2>&1 &
  PIDS+=($!)
  wait_health vision "http://localhost:$VISION_PORT/health"

  log "Arrancando agente en :$AGENT_PORT …"
  VISION_API_URL="${VISION_API_URL:-http://localhost:$VISION_PORT}" \
    uv run uvicorn services.agent.app:app --host 0.0.0.0 --port "$AGENT_PORT" \
    >"$LOG_DIR/agent.log" 2>&1 &
  PIDS+=($!)
  wait_health agent "http://localhost:$AGENT_PORT/health"

  cat <<EOF

  Sistema arriba
  ─────────────────────────────────────────────────────
  API de visión  http://localhost:$VISION_PORT/docs
  Agente (chat)  http://localhost:$AGENT_PORT/
  Logs           $LOG_DIR/{vision,agent}.log
  Ctrl+C para detener ambos servicios.

EOF
  wait
}

case "$MODE" in
  data) step_data ;;
  prepare) step_sync; step_data; step_model ;;
  all) step_sync; step_data; step_model; step_services ;;
esac
