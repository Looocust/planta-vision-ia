# SOP-GRP-02 — Diagnóstico de fallos de agarre (gripper de vacío)

Versión 2.0 · Dueño: Mantenimiento.

## [GRP-02.1] Información mínima para diagnosticar
Antes de diagnosticar, reunir: línea, ventosa/gripper afectado (G1/G2), ventana de tiempo, tasa de fallos por hora, presión de vacío registrada, SKUs afectados y si el objeto se suelta al tomarlo o durante el transporte.

## [GRP-02.2] Árbol de diagnóstico
- Fallos concentrados en una sola ventosa + vacío menos negativo que -55 kPa → fuga o desgaste de ventosa (ver VAC-03.2).
- Fallos distribuidos en todas las ventosas + vacío normal → revisar el ángulo de aproximación (posible error de ángulo de visión) y el estado del modelo.
- Fallos con un SKU específico → revisar superficie/porosidad del SKU y perfil de agarre.

## [GRP-02.3] Acciones correctivas
1. Reemplazar la ventosa con desgaste visible (tiempo estimado: 15 min, requiere línea pausada).
2. Verificar ajuste de la abrazadera y la manguera.
3. Ejecutar 50 ciclos de prueba; tasa de fallo aceptable < 1 %.

## [GRP-02.4] Escalamiento
Si la tasa de fallos supera 5 % por más de 2 horas o hay riesgo de caída de piezas sobre personas, escalar a `mantenimiento_nivel_2` y considerar pausar la línea (SOP-LIN-01).
