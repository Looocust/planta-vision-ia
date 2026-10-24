## 1) Veredicto: **CAMBIOS REQUERIDOS**

El diseño central del Reto 6 cumple: Azure tiene servicios concretos, alternativas razonadas y separación edge determinístico/nube agéntica. Los bloqueos están en el empaquetado y en inconsistencias entre arquitectura, costos y evidencia.

No modifiqué archivos. `uv run python` falló por permisos de caché/temporales; leí el XLSX con `openpyxl 3.1.5` usando `.venv/bin/python -B`. Docker no está instalado: no certifico el despliegue integral.

## 2) Requisitos

Las referencias a la prueba corresponden a `docs/validacion/prueba_tecnica.md`.

| Requisito aplicable | Estado | Evidencia |
|---|---|---|
| Azure con servicios concretos y trade-offs — prueba:226 | ✅ | `docs/cloud/azure_architecture.md:104,147,179,189`: IoT Edge, Container Apps, PostgreSQL y Search, con alternativas y razones. |
| Separación determinístico/agéntico por latencia, costo y riesgo — prueba:60 | ✅ | `docs/architecture.md:15`; `azure_architecture.md:23`. El LLM queda fuera del ciclo del robot. |
| Arquitectura + modelo de costos — prueba:244 | ✅ | Documentos presentes; XLSX con siete hojas y 282 fórmulas. Base recalculada: **Azure USD 518,58**, **AWS USD 396,77**, coherente con `finops.md:25`. |
| Supuestos y precios identificados | ⚠️ | Supuestos explícitos en `finops.md:17,21`; precios estimados diferenciados. Persisten problemas regionales, de dimensionamiento y de sensibilidad descritos abajo. |
| Visión con API independiente del agente — prueba:211 | ✅ | `docker-compose.yml:21,37`: servicios separados, puertos 8001/8002, coherentes con `CONTRACTS.md:12`. |
| Organización y configuración separada — prueba:214 | ✅ | `Dockerfile:29`; `.env.example`; carpetas `src/`, `services/` y `configs/`. |
| README raíz con ejecución e índice — prueba:215,247 | ❌ | `README root []`; `git ls-files '*README*' '*readme*'` sin resultados. Las instrucciones están en `docs/architecture.md:81`. |
| `.env.example` sin secretos — prueba:215 | ✅ | `.env.example:4`: claves vacías y tokens de ejemplo. |
| Artefacto versionado — prueba:216 | ⚠️ | Existe `models/vision-1.0.0/`, pero `git status --short models` devuelve `?? models/`; no consta incorporado a la entrega versionada. |
| Empaquetado reproducible y despliegue de un comando — prueba:209,217 | ⚠️ | `Makefile:35` ofrece `make up`; `run_local.sh:167` prepara y arranca todo. Hay configuración incorrecta y comprobaciones de disponibilidad insuficientes. `bash -n` pasa; Docker está declarado sin probar. |
| Arquitectura y ambas secuencias — prueba:218 | ✅ | `docs/diagrams/01_arquitectura_local.md:7`, `02_secuencia_imagen_robot.md:7` y `03_secuencia_aprobacion_humana.md:8`. Incluyen HOLD, aprobación humana y separación solicitante/aprobador. |

Libro revisado: :codex-file-citation{path="/Users/nico/Documents/IA/docs/cloud/finops_model.xlsx" artifact_kind="workbook" purpose="source"}

## 3) Alucinaciones o afirmaciones sin respaldo

- **“Medido ≈12 ms” no está respaldado por los resultados entregados.** `docs/architecture.md:28`, diagrama `02:50` y `azure_architecture.md:342` lo presentan como medición. `docs/report/results.json:2847` registra **p50 16,82 ms, p95 39,48 ms y media 19,86 ms** para el pipeline clásico completo. Falta identificar otra medición comparable o actualizar los documentos.

- **“La diferencia entre proveedores es <2% del ahorro laboral” es incorrecto para Colombia.** `azure_vs_aws.md:5`: la diferencia recalculada es USD 121,81 frente a USD 1.060,75 de ahorro mensual: **11,48%**. Solo resulta aproximadamente cierto para España.

- **Comparaciones de calidad sin evaluación citada:** “AI Search mejor calidad híbrida” (`azure_vs_aws.md:19`) y “Nova Lite […] menor calidad de tool-calling” (`aws_architecture.md:94`). Son hipótesis de selección, no resultados demostrados.

- **Citas SOP inexistentes en el ejemplo:** `03_secuencia_aprobacion_humana.md:29` referencia `SOP-MNT-004` y `SOP-ML-002`, ausentes de `docs/sop/`. Las cifras del ejemplo están identificadas como ilustrativas; estas referencias deberían existir o marcarse también como ficticias.

- **“API verificada” tiene trazabilidad limitada.** `build_finops.py:305` y `:540` guardan URL general y fecha, pero no medidores ni respuestas consultadas. No lo clasifico como precio falso; no pude reverificar la API durante esta revisión.

El 95% ACT, throughput, salarios, CAPEX y tokens **sí están marcados como supuestos**. No son métricas inventadas; llamar “conservador” al 95% (`build_finops.py:110`) carece de evidencia.

## 4) Errores técnicos

1. **Alta — La configuración entregada rompe las imágenes de referencia.**  
   [.env.example:21](/Users/nico/Documents/IA/.env.example:21) y `Dockerfile:55` fijan `DATA_DIR=data/raw`, mientras el contrato ubica las imágenes en `data/raw/coil-100` (`CONTRACTS.md:23`). La API concatena directamente el identificador (`services/vision_api/app.py:197`). Reproducción: archivo existente, pero **`GET /v1/images/obj1__0 → 404`**.

2. **Alta — El arranque puede anunciar éxito sin inferencia disponible.**  
   [run_local.sh:95](/Users/nico/Documents/IA/scripts/run_local.sh:95) acepta cualquier archivo como modelo presente. Los healthchecks solo verifican HTTP exitoso (`:118`, `docker-compose.yml:31`), pero sin predictor `/health` devuelve **200 con `status=degraded`**. Deben comprobar artefactos completos y disponibilidad real.

3. **Alta — El costo europeo no corresponde al diseño europeo.**  
   `Comparativo!D6:E8` reutiliza precios East US 2/us-east-1, aunque `azure_architecture.md:279` prescribe Spain Central y Data Zone UE. El propio XLSX advierte diferencias regionales (`build_finops.py:406`). Además, se presupuesta una réplica del agente (`:311`), pero se promete redundancia de zona (`azure_architecture.md:328`); Microsoft recomienda al menos dos. Si se implementan los Private Endpoints prometidos para Container Apps, falta también su cargo específico. [Redundancia](https://learn.microsoft.com/en-us/azure/reliability/reliability-container-apps), [facturación privada](https://learn.microsoft.com/en-us/azure/container-apps/private-endpoints-with-dns).

4. **Media — Fórmulas FinOps con dependencias y unidades incorrectas.**
   - `Sensibilidad!D21:D24` tiene precios del modelo grande hardcodeados (`build_finops.py:530`). Duplicar `Azure!C36` deja `D22=61,85`; debería subir a **97,60**.
   - `fte_residual=0,1 FTE` (`:112`) se transforma en **59,8 horas/mes** (`:438`); según sus propios denominadores, 0,1 FTE serían **19 horas en Colombia y 14,67 en España**. La fórmula representa una fracción de cobertura del puesto.
   - Cambiar automatización en Sensibilidad no actualiza almacenamiento (`:483,496`). Al 80%, el error base es pequeño: **USD 0,85/mes**.

5. **Media — IoT Hub está subdimensionado para el flujo dibujado.**  
   `azure_architecture.md:79,224` enruta imágenes por IoT Hub y afirma que S1 admite unas cien estaciones. La fórmula (`build_finops.py:331`) solo cuenta 3.000 mensajes diarios por estación. Con sus supuestos, se suben unas 994 imágenes/día de 150 KB; cada una consume al menos 38 unidades de 4 KB. Resultado aproximado: **40.757 unidades/día/estación**, unas **nueve estaciones por S1**, antes de otros mensajes. [Cuotas oficiales](https://learn.microsoft.com/en-us/azure/iot-hub/iot-hub-devguide-quotas-throttling).

6. **Media — Dos capacidades cloud están descritas incorrectamente.**
   - `azure_architecture.md:209` asigna LRS a imágenes y ZRS/GZRS al “contenedor” de auditoría. La redundancia se configura **por cuenta**: requieren cuentas separadas. Archive tampoco admite ZRS/GZRS. [Redundancia](https://learn.microsoft.com/en-us/azure/storage/common/storage-redundancy), [Archive](https://learn.microsoft.com/en-us/azure/storage/blobs/access-tiers-overview).
   - `aws_architecture.md:93,109` presupone autenticación ALB OIDC directamente con Identity Center. Identity Center no admite aplicaciones empresariales OIDC como propone el documento; debe concretarse Entra OIDC directo o un puente compatible. Asimismo, Stream Manager no exporta directamente a IoT Core como indica `:82`. [Identidad](https://aws.amazon.com/iam/identity-center/faqs/), [destinos Stream Manager](https://docs.aws.amazon.com/greengrass/v2/developerguide/stream-export-configurations.html).

7. **Baja — Referencia laboral desactualizada.**  
   `Supuestos!F22` usa jornada colombiana de 44 horas con fecha octubre de 2026. Desde el 15 de julio de 2026 corresponde revisar el escenario con **42 horas**. [Circular oficial](https://normativa.colpensiones.gov.co/compilacion/docs/circular_mintra_0101_2025.htm).

Las 282 fórmulas no presentan referencias rotas en la evaluación independiente. Todas carecen de valores cacheados; tienen `fullCalcOnLoad=True`. No verifiqué recálculo en Excel. No audité entrenamiento, particiones ni selección de umbrales, conforme al alcance.

## 5) Cambios priorizados

1. Corregir `DATA_DIR` y verificar recuperación de referencias con la configuración entregada.
2. Validar ambos archivos del modelo y exigir readiness real antes de anunciar éxito.
3. Añadir README raíz y asegurar que un clon contiene o recupera los artefactos necesarios.
4. Recalcular costos por región y por la redundancia/red privada realmente propuestas.
5. Conectar todas las sensibilidades a sus entradas y corregir la unidad FTE.
6. Redimensionar IoT Hub incluyendo imágenes, o diseñar explícitamente su subida directa a Blob.
7. Corregir cuentas Storage, autenticación AWS y destinos de Stream Manager.
8. Actualizar latencias, porcentajes comparativos, referencias SOP y afirmaciones de calidad.
9. Actualizar referencias temporales y conservar consultas/medidores de precios verificables.
10. Ejecutar un smoke test desde un clon limpio; probar Docker antes de declararlo reproducible.