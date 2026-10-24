# Diagrama 1 — Arquitectura del sistema (demo local)

Qué corre en la demo y cómo se conecta. Las piezas están separadas igual que en producción:
la visión es un servicio propio (en producción vive en el borde, junto a la línea) y el agente
es otro servicio que la consume como una herramienta más.

```mermaid
flowchart LR
    subgraph USR["Usuarios (navegador)"]
        U1["Supervisor / Calidad /<br/>Mantenimiento / ML / Gerente"]
    end

    subgraph AG["Agente · services/agent · :8002"]
        UI["UI de chat mínima<br/>GET /"]
        API["API FastAPI<br/>/v1/chat · /v1/uploads · /v1/approvals"]
        LG["Grafo LangGraph<br/>(planifica y elige tools)"]
        POL["Política determinista<br/>RBAC + reglas de riesgo"]
        EXE["Ejecutor determinista<br/>(acciones aprobadas)"]
        SOP["Recuperación de SOP<br/>docs/sop con citas"]
        PLANT[("Backends simulados SQLite<br/>métricas · cámara · robot · tickets ·<br/>cola · aprobaciones · auditoría")]
    end

    subgraph VI["Visión · services/vision_api · :8001"]
        VAPI["FastAPI<br/>/v1/predict · /v1/model · /v1/images"]
        QG["Compuerta de calidad<br/>brillo · nitidez · tamaño"]
        CLS["Clasificador clásico<br/>características + SVM/kNN"]
        ANG["Estimador de ángulo<br/>+ incertidumbre"]
        DEC["Decisión ACT / REVIEW / REJECT<br/>+ robot_command"]
        ADM["Admin con X-Admin-Token<br/>umbrales · staging SKU · promote"]
        ART[("models/vision-x.y.z<br/>artefacto versionado")]
    end

    LLM["Proveedor LLM<br/>Gemini en la demo<br/>Azure OpenAI / Bedrock en nube"]

    U1 --> UI --> API --> LG
    LG <--> LLM
    LG --> POL
    POL -->|"permitido y de bajo riesgo"| PLANT
    POL -->|"riesgo alto: interrupt"| EXE
    EXE --> PLANT
    EXE -->|"cambio de umbral / promote"| ADM
    LG --> SOP
    LG -->|"inspeccionar imagen"| VAPI
    VAPI --> QG --> CLS --> ANG --> DEC
    ART --> CLS
    ADM --> ART
```

**Lectura en una frase:** el LLM solo *propone*; lo que toca la planta pasa por una política
determinista y, si es riesgoso, por una aprobación humana antes de que un ejecutor
determinista lo aplique y lo deje auditado.
