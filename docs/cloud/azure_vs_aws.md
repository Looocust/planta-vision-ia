# Azure vs AWS para este sistema

> **Recomendación: Azure**, con una salvedad honesta: en el modelo de costos AWS sale **~USD 120/mes más barato por
> planta** a precio de lista, porque elegimos servicios distintos (no porque el proveedor sea más barato).
> Esa diferencia es **< 2% del ahorro laboral** que genera una sola estación, así que no decide el caso.
> Deciden el encaje con el ecosistema Microsoft del cliente, la gestión unificada del borde y la residencia
> de datos del LLM.

## 1. Mapeo de servicios

| Necesidad | Azure | AWS | Comentario |
|---|---|---|---|
| Runtime de borde | IoT Edge (o AKS Edge Essentials / Azure IoT Operations) | IoT Greengrass V2 | Ambos maduros. Greengrass trae Stream Manager; IoT Edge trae edgeHub |
| Registro / aprovisionamiento de dispositivos | IoT Hub + DPS | IoT Core + Fleet Provisioning | Equivalentes |
| Gestión de seguridad del PC de borde | Azure Arc + Defender for Servers | Systems Manager híbrido (+ GuardDuty no cubre on-prem) | **Ventaja Azure**: un solo plano (Arc) para inventario, parches, políticas y EDR |
| Hospedar agente | Container Apps (escala a cero) | ECS Fargate + ALB | Container Apps más simple y barato en tráfico de turnos |
| LLM | Azure OpenAI (GPT-4.1 / familias nuevas) | Bedrock (Claude, Nova, Llama, …) | Bedrock da más variedad de modelos; Azure OpenAI da Data Zone UE/EE. UU. muy clara |
| Guardas del LLM | Content filters + Prompt Shields | Bedrock Guardrails | Equivalentes |
| Recuperación de SOP | AI Search (híbrido + semántico) | Bedrock Knowledge Bases sobre Aurora pgvector | AI Search mejor calidad híbrida; KB+pgvector más barato |
| Base de datos | PostgreSQL Flexible | Aurora PostgreSQL Serverless v2 | Mismo motor → código portable |
| Objetos | Blob (Hot/Cool/Archive, inmutabilidad) | S3 (Standard/IA/Deep Archive, Object Lock) | Equivalentes |
| MLOps | Azure ML (Registry, MLflow, Pipelines) | SageMaker AI (Model Registry, MLflow, Pipelines) | Ambos con MLflow → el repo es portable |
| Secretos | Key Vault | Secrets Manager | |
| Identidad | Entra ID (app roles, PIM) | IAM Identity Center federado con Entra | **Ventaja Azure** si la empresa ya usa Microsoft 365 |
| Observabilidad | Azure Monitor + App Insights + Log Analytics | CloudWatch + X-Ray/ADOT | |
| Seguridad | Defender for Cloud | GuardDuty + Security Hub + Inspector | |
| Aislamiento por planta | RG (o suscripción) por planta | Cuenta por planta (Control Tower) | |

## 2. Comparación por criterio

| Criterio | Azure | AWS | Ganador |
|---|---|---|---|
| **Costo nube / planta / mes** (1 estación, lista) | ≈ USD 519 | ≈ USD 397 | AWS (por elección de servicios: AI Search +74, PG con HA +100 vs Aurora 1 ACU) |
| Costo LLM a igual volumen | ≈ USD 20 (gpt-4.1-mini) | ≈ USD 36 (Claude Haiku 4.5) | Azure |
| Confianza en los precios del modelo | Mayoría **verificada** en la API pública de precios | **Estimados** de páginas públicas | Azure (en este entregable) |
| Esfuerzo operativo | Menor: Container Apps escala a cero, Arc unifica el borde | Algo mayor: ALB, NAT, endpoints por AZ | Azure |
| Madurez en el borde | Alta; Microsoft orienta lo nuevo a **Azure IoT Operations** (Arc + K8s) → riesgo de roadmap para IoT Edge a largo plazo | Alta; Greengrass estable, aunque AWS ha retirado algunos servicios IoT de alto nivel | Empate (vigilar roadmaps) |
| Disponibilidad de LLM | GPT y modelos de Foundry; Data Zone UE | Claude, Nova, Llama, Mistral…; regiones UE | AWS en variedad; Azure en simplicidad de residencia |
| Lock-in | Medio: contenedores, PostgreSQL y MLflow son portables; IoT Edge y Entra no tanto | Medio: equivalente con Greengrass/Bedrock | Empate |
| Ecosistema empresarial | Entra ID, Microsoft 365/Teams (notificar aprobaciones), Power BI, Fabric | Fuerte en nativos cloud | **Azure** para una empresa de consumo masivo típica |
| Lo que pide la prueba | Diseño Azure explícito | Complementario | Azure |

## 3. Recomendación y razones

1. **Identidad y colaboración:** casi todas las empresas de consumo masivo ya tienen Entra ID y Teams. Los roles del
   sistema se vuelven grupos existentes y las aprobaciones pueden notificarse en Teams. En AWS habría que federar.
2. **Un solo plano para el borde:** Azure Arc + Defender + IoT Hub cubren inventario, parches, EDR y despliegues de los PC de planta.
3. **Residencia del LLM** con Data Zone UE para una planta en España, sin pagar capacidad dedicada.
4. **El costo no decide:** la diferencia de ~USD 120/mes es de diseño. Si se quiere igualar, Azure puede usar
   pgvector en lugar de AI Search (−74) y PostgreSQL sin HA en piloto (−130). Lo que realmente mueve el caso es el
   % automatizado y el costo laboral local (ver hoja *Sensibilidad*).

**Cuándo elegiría AWS:** si la empresa ya opera en AWS (cuentas, landing zone, equipo), si quiere libertad para
cambiar de proveedor de LLM (Claude/Nova/Llama) o si la planta ya usa Greengrass o SiteWise.

**Portabilidad como seguro:** el diseño deja el núcleo portable (contenedores, PostgreSQL, MLflow, LangGraph con
proveedor de LLM configurable por variable de entorno). Cambiar de nube es reescribir infraestructura, no la aplicación.
