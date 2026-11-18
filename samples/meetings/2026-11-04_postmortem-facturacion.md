# Post-mortem: facturas duplicadas del 3 de noviembre

**Fecha:** 2026-11-04
**Participantes:** Valeria Montes, Jorge Atencio, Carlos Ruiz, Fiorella Campos
**Facilitador:** Valeria Montes

## Línea de tiempo

- 09:10 Soporte reporta clientes con dos facturas por el mismo pedido.
- 09:40 Se pausa el job de emisión.
- 11:15 Se confirma que el reintento del webhook creó comprobantes duplicados.

## Decisiones

- Decisión: se desactiva el reintento automático del webhook de emisión hasta tener idempotencia.
- Se acordó que toda nota de crédito por duplicados la emite Finanzas, no Soporte.

## Seguimiento

- [x] Pausar el job de emisión de comprobantes (Jorge) - hecho el 3 de noviembre
- [ ] Implementar clave de idempotencia en el endpoint de emisión | resp: Jorge | fecha: 2026-11-13
- [ ] Reconciliar las facturas emitidas entre el 1 y el 3 de noviembre con Contabilidad | responsable: Fiorella | fecha: mañana
- TODO(carlos): agregar prueba de regresión para el webhook duplicado, fecha: 2026-11-11
- TODO revisar las alertas de volumen anormal de emisión
- Pendiente: comunicar a los clientes afectados las notas de crédito (responsable: Valeria, para el viernes) (en curso)
- Pendiente: definir si se reembolsa la comisión del medio de pago

## Dudas

- Duda: ¿Cuántos clientes recibieron el comprobante duplicado antes de las 09:40?
- ¿Se puede anular un comprobante ya aceptado por SUNAT sin nota de crédito?
