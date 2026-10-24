# SOP-SKU-05 — Alta de un nuevo SKU en el modelo de visión

Versión 1.0 · Dueño: Ingeniería ML + Calidad.

## [SKU-05.1] Requisitos de vistas
Mínimo 12 vistas del SKU, con cobertura angular ≥ 270° (idealmente 72 vistas cada 5°), fondo y cámara de producción, sin imágenes oscuras ni borrosas.

## [SKU-05.2] Incorporación en staging
Las vistas se cargan en un ambiente de staging, que genera un modelo candidato. Nunca se incorpora directamente a producción.

## [SKU-05.3] Verificación de desempeño
El candidato debe mantener accuracy ≥ 0.90 en el set de validación y no degradar más de 1 punto a los SKU existentes.

## [SKU-05.4] Promoción
La promoción a producción se solicita con `promote_model` y requiere aprobación de Calidad o del gerente de planta (ver ML-04.3).
