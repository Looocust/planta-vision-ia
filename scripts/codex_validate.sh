#!/usr/bin/env bash
# Validación independiente con Codex (otro proveedor de modelo) de una parte del proyecto
# contra el texto de la prueba. Codex corre en modo solo lectura y escribe su informe en
# docs/validacion/<alcance>.md.
# Uso: scripts/codex_validate.sh <alcance> "<qué revisar: rutas y requisitos>"
set -euo pipefail
cd "$(dirname "$0")/.."
scope="$1"; focus="$2"
out="docs/validacion/${scope}.md"
prompt=$(cat <<PROMPT
Eres un auditor técnico independiente (nivel Senior+) revisando un proyecto de prueba técnica de AI Engineer.
Fuente de verdad: docs/validacion/prueba_tecnica.md (texto de la prueba). Contratos del sistema: docs/CONTRACTS.md.

Alcance de esta revisión: ${focus}

Instrucciones:
- Lee el código y los documentos del alcance. Puedes ejecutar comandos de solo lectura (ls, cat, grep, uv run pytest -q, uv run python -c ...) para verificar afirmaciones.
- Verifica cada requisito de la prueba que aplique a este alcance: ✅ cumple / ⚠️ parcial / ❌ falta, con evidencia concreta (archivo:línea o salida de comando).
- Busca ALUCINACIONES: cifras, servicios, precios, métricas o afirmaciones en los documentos que no estén respaldadas por código, resultados medidos (docs/report/results.json, eval/agent/results) o fuentes citadas. Señala cada una.
- Busca errores técnicos (bugs, fugas de datos de test a entrenamiento, umbrales elegidos con el test, inconsistencias entre documentos y código, contratos incumplidos).
- No modifiques archivos.
- Responde en español, en Markdown, con: 1) Veredicto (APROBADO / CAMBIOS REQUERIDOS), 2) Tabla de requisitos con estado y evidencia, 3) Alucinaciones o afirmaciones sin respaldo, 4) Errores técnicos, 5) Cambios priorizados (máx. 10, del más al menos importante). Sé concreto y breve.
PROMPT
)
codex exec --sandbox read-only -C "$PWD" --skip-git-repo-check -o "$out" "$prompt" > "docs/validacion/.${scope}.log" 2>&1
echo "Informe: $out"
