# Planificación Sprint 47 — Plataforma de Pagos

Fecha: 2026-11-02
Lugar: Sala Miraflores / Teams
Asistentes: Carlos Ruiz (CR), Lucía Paredes, Mariano Quispe (Mari), Rosa Vidal, Diego Salinas

## Resumen

Se revisó el backlog del sprint 47. El foco es cerrar la conciliación automática de pagos con el banco
y dejar listo el checklist de release antes del congelamiento de código.

## Decisiones

- Se congela el código del sprint 47 el viernes 13 de noviembre a las 18:00.
- Decisión: el reporte de conciliación se genera cada noche a las 02:00 y no en tiempo real.
- Acuerdo: Rosa Vidal valida los casos de prueba de reembolsos antes de pasar a UAT.

## Acciones

- Acción: Preparar el script de conciliación bancaria con el formato nuevo del banco | responsable: Carlos | fecha: 2026-11-06
- Acción: Actualizar el checklist de release con los pasos de rollback | responsable: Lucía Paredes | fecha: 09/11/2026
- Acción (Mariano): Configurar las alertas de errores de pasarela en el monitoreo | fecha: 2026-11-10
- Acción: Documentar el flujo de reembolsos en el wiki del equipo
  responsable: Rosa
  fecha: antes del viernes
- Acción: Revisar los permisos del usuario de servicio de la base de datos | responsable: DS | plazo: 12 de noviembre
- Acción: Agendar la demo con el área de Finanzas | prioridad: alta

## Preguntas abiertas

- ¿Quién será el contacto del banco para las pruebas de contingencia?
- Pregunta: ¿Se mantiene el límite de 3 reintentos por transacción rechazada? (responsable: Carlos)
