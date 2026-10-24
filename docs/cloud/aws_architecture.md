# Arquitectura de producción en AWS

> **Resumen.** Misma separación que en Azure: el ciclo imagen → robot corre **en el borde** con
> **AWS IoT Greengrass V2** en un PC industrial; la nube aloja el asistente (ECS Fargate + Amazon Bedrock),
> el estado y la auditoría (Aurora PostgreSQL Serverless v2), la recuperación de SOP (Bedrock Knowledge Bases
> sobre pgvector), las imágenes (S3 con ciclo de vida) y el MLOps (SageMaker Model Registry). Cada planta es una
> **cuenta AWS propia** dentro de AWS Organizations. Costo de nube estimado: **~USD 400/mes por planta con 1 estación**
> (precios de lista **no verificados contra API**, ver `finops_model.xlsx`).

Este documento sigue la misma estructura que [`azure_architecture.md`](azure_architecture.md); donde la
justificación es idéntica (qué va en el borde y por qué, presupuesto de latencia, roles) se resume y se remite a él.

---

## 1. Principio de diseño

Idéntico a Azure: **determinista y local** para todo lo que mueve el robot; **agéntico y en la nube** para
asistir a las personas; **política determinista + aprobación humana** entre el LLM y cualquier acción de riesgo.
Ver [tabla en Azure §1](azure_architecture.md#1-principio-de-diseño-qué-va-dónde).

## 2. Diagrama

```mermaid
flowchart TB
    subgraph PLANTA["Planta (red OT) · una por estación"]
        CAM["Cámara + iluminación"]
        subgraph IPC["PC industrial · Ubuntu + IoT Greengrass V2"]
            VM["Componente visión<br/>(contenedor services/vision_api)"]
            PA["Componente adaptador PLC<br/>OPC UA"]
            ANON["Componente anonimizador<br/>ROI + difuminado"]
            SM["Stream Manager<br/>buffer offline"]
            LUI["UI local de revisión"]
        end
        PLC["PLC / robot"]
        CAM --> VM --> PA --> PLC
        VM --> ANON --> SM
        VM --> LUI
    end

    subgraph ORG["AWS Organizations"]
        subgraph SHARED["Cuenta plataforma compartida"]
            IOTC["AWS IoT Core<br/>+ Fleet Provisioning"]
            ECR["Amazon ECR"]
            SMR["SageMaker AI<br/>Model Registry + Pipelines + MLflow"]
            BR["Amazon Bedrock<br/>Claude Haiku 4.5 / Sonnet 4.5<br/>+ Guardrails"]
        end
        subgraph STAMP["Cuenta planta-ID (estampa)"]
            ALB["ALB + autenticación OIDC"]
            ECS["ECS Fargate<br/>agente · visión no-tiempo-real · UI"]
            AUR["Aurora PostgreSQL Serverless v2<br/>estado · aprobaciones · auditoría ·<br/>checkpoints · pgvector"]
            KB["Bedrock Knowledge Base<br/>SOP de la planta"]
            S3["S3<br/>Standard→IA→Deep Archive<br/>auditoría con Object Lock"]
            SEC["Secrets Manager"]
            LBD["Lambda: router de eventos"]
            CW["CloudWatch + X-Ray (ADOT)"]
        end
        IDC["IAM Identity Center<br/>federado con Entra ID corporativo"]
        GD["GuardDuty · Security Hub · Inspector"]
    end

    SM -- "MQTT/HTTPS saliente" --> IOTC
    IOTC -- "regla IoT: evidencia" --> S3
    IOTC -- "regla IoT: eventos" --> LBD --> AUR
    SMR -- "despliegue Greengrass por thing group<br/>canario → general" --> IPC
    ECR -. "pull" .-> IPC
    USERS["Usuarios de planta"] -- "SSO" --> IDC
    USERS -- "HTTPS" --> ALB --> ECS
    ECS --> BR
    ECS --> KB --> AUR
    ECS --> AUR
    ECS --> S3
    ECS --> SEC
    ECS -. "trazas" .-> CW
    GD -. "detección" .-> STAMP
```

## 3. Borde: AWS IoT Greengrass V2

| | **Elegido: IoT Greengrass V2** | Alternativa: EKS Anywhere / ECS Anywhere | Alternativa: AWS Outposts / Snow |
|---|---|---|---|
| Encaje | Componentes (contenedores o procesos) desplegados por *thing groups*; ideal para 3-4 módulos | Útil si la planta ya opera K8s/ECS | Sobredimensionado y caro |
| Offline | **Stream Manager** con buffer local y exportación a S3/Kinesis/IoT al reconectar | Construirlo | Sí |
| Costo | ~USD 0,16/dispositivo/mes (estimado) | Licencias/soporte | Alto |

Componentes equivalentes a los de Azure: `vision` (imagen con el modelo dentro), `plc-adapter`, `anonymizer`,
UI local de revisión. Gestión del SO del PC con **Systems Manager** (instancias híbridas) y aprovisionamiento con
**Fleet Provisioning** + certificados X.509 (o TPM vía PKCS#11). Red OT idéntica (IEC 62443, solo saliente).

## 4. Plano de nube

| Necesidad | Servicio y tier | Por qué | Rechazado y por qué |
|---|---|---|---|
| Hospedar agente, UI y visión cloud | **ECS on Fargate** (0,5 vCPU / 1 GB) detrás de **ALB** con autenticación OIDC | Sin servidores que parchear; ALB autentica contra Identity Center/Entra antes de llegar a la app | **App Runner** (más simple, pero menos control de red y de autenticación); **EKS** (operar K8s para 2-3 servicios); **Lambda** (conversaciones largas con estado) |
| LLM | **Amazon Bedrock**: Claude Haiku 4.5 por defecto, Claude Sonnet 4.5 para diagnósticos | Modelos fuertes en uso de herramientas; pago por token; datos no usados para entrenar; endpoint privado (PrivateLink) | Modelos abiertos en GPU propia (costo fijo); Amazon Nova Lite (más barato, menor calidad de tool-calling: evaluar con `eval/agent`) |
| Protección del LLM | **Bedrock Guardrails** (filtros de contenido, ataques de prompt, temas denegados) | Capa equivalente a Prompt Shields | — |
| Recuperación de SOP | **Bedrock Knowledge Bases** con vector store **Aurora PostgreSQL (pgvector)** | Reutiliza la base que ya pagamos; devuelve citas | **OpenSearch Serverless** (mínimo ~USD 350/mes, desproporcionado para pocos SOP); **Kendra** (licencia alta) |
| Estado, aprobaciones, auditoría, checkpoints LangGraph | **Aurora PostgreSQL Serverless v2** (0,5–2 ACU, réplica lectora en otra AZ) | Relacional y transaccional (mismas razones que en Azure); escala con la carga de turnos; requisito de Bedrock KB | **DynamoDB** (equivalente a Cosmos: sin joins ni restricciones para "aprobador ≠ solicitante"); **RDS PostgreSQL** (válido y algo más barato fijo, pero no sirve de vector store para KB) |
| Imágenes | **S3** con lifecycle: Standard 30 d → Standard-IA → Glacier Deep Archive a 180 d → expira a 3 años; auditoría con **Object Lock** (WORM) | Barato, maduro | — |
| MLOps | **SageMaker AI**: MLflow gestionado, Pipelines (CPU, escala a cero), **Model Registry** con estados *PendingManualApproval → Approved* | El estado de aprobación del registro encaja con nuestra aprobación humana | Endpoints de SageMaker para inferencia en tiempo real (la inferencia vive en el borde) |
| Telemetría y dispositivos | **IoT Core** (reglas → S3 / Lambda), **IoT Device Defender** | Pago por mensaje: muy barato a esta escala | **Kinesis** (como Event Hubs: solo con >20 plantas o analítica en streaming) |
| Secretos | **Secrets Manager** | Rotación integrada | Parameter Store (válido para config no secreta) |
| Identidad | **IAM Identity Center** federado con el Entra ID corporativo para personas; **roles IAM** para servicios | Una sola identidad corporativa; roles de app como grupos | Cognito como directorio propio (otra base de usuarios que mantener) |
| Observabilidad | **CloudWatch** (logs, métricas, alarmas) + **X-Ray / ADOT** para trazas del agente | Nativo | — |
| Seguridad | **GuardDuty**, **Security Hub**, **Inspector** (imágenes ECR), **VPC endpoints** | Postura y detección | — |

## 5. Identidad y roles

Los seis roles (`operator`, `supervisor`, `quality`, `maintenance`, `ml_engineer`, `plant_manager`) se modelan como
**grupos** sincronizados desde Entra ID a IAM Identity Center; el ALB entrega los claims al agente, que filtra tools y
aplica la política. Para infraestructura, *permission sets* por rol (p. ej. `ml_engineer` → acceso a SageMaker en la
cuenta compartida; `plant_manager` → Billing/Cost Explorer de su cuenta). Misma regla de separación de funciones.

## 6. Aislamiento multi-planta

**Cuenta AWS por planta** (vending con **Control Tower**) dentro de una OU "Plantas", más una cuenta de plataforma
compartida. En AWS la cuenta es la frontera natural de aislamiento, permisos y facturación; con Control Tower crear
una cuenta nueva es barato y repetible. Equivale a la estampa por RG/suscripción de Azure. Planta en España →
región **eu-south-2 (Spain)**.

## 7. MLOps

Mismo flujo que Azure §7: staging → puertas automáticas (exactitud del SKU nuevo, no regresión, latencia) →
aprobación humana (estado *Approved* del Model Registry, aprobador ≠ solicitante) → imagen en ECR →
**despliegue de Greengrass a un thing group "canario"** (sombra → activo) → despliegue general o **rollback** al
despliegue anterior. Ver [`04_secuencia_onboarding_sku.md`](../diagrams/04_secuencia_onboarding_sku.md).

## 8. Seguridad y cumplimiento

Mismos controles que Azure §8 con sus equivalentes: Bedrock Guardrails ↔ Prompt Shields; Object Lock ↔ inmutabilidad Blob;
Secrets Manager ↔ Key Vault; GuardDuty/Inspector ↔ Defender; SCPs de Organizations para impedir desactivar
CloudTrail o sacar datos de la región. Anonimización en el borde antes de subir. Ley 1581 / RGPD igual que en Azure.

## 9. HA/DR

La línea no depende de la nube (inferencia local + Stream Manager). Aurora con réplica en otra AZ (failover < 1 min) y
backups continuos (PITR); ECS con tareas en 2 AZ; para desastre regional, redespliegue por IaC (CDK/Terraform) y
restauración de snapshot copiado a otra región. Objetivos iguales a los de Azure §9.

## 10. Latencia

Idéntica a Azure §10 (el camino crítico no toca la nube): **≈ 25–60 ms estimados hasta el PLC, presupuesto < 100 ms**.

## 11. Costos (resumen, todos estimados)

| Bloque | USD/mes |
|---|---|
| Plataforma fija (Aurora 2×1 ACU, Fargate, ALB, NAT, VPC endpoints, GuardDuty, …) | ≈ 350 |
| Por estación (S3, IoT Core, Greengrass, SSM) | ≈ 11 |
| LLM (Claude Haiku 4.5 + 15% Sonnet 4.5) | ≈ 36 |
| **Total** | **≈ 397** |

Comparación detallada y por qué AWS sale más barato en este modelo: [`azure_vs_aws.md`](azure_vs_aws.md).
