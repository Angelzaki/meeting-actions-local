# Revisión mensual de seguridad

Fecha: 9 de noviembre de 2026

Asistentes:
- Ing. Sofía Landa (SL) - CISO
- Marcos Delgado (Marco) - Infraestructura
- Elena Ramos - Cumplimiento
- Carlos Ruiz (CR) - Plataforma

## Hallazgos

El escaneo mensual encontró 4 cuentas de administrador sin MFA y 2 servidores sin parches críticos.
El respaldo de la base de producción falló dos noches seguidas por falta de espacio.

## Acuerdos

- Acuerdo: MFA obligatorio para toda cuenta con rol administrador desde el 1 de diciembre.
- Se decidió que los respaldos de producción se verifican con una restauración de prueba cada mes.
- Quedó acordado que Cumplimiento aprueba cualquier excepción de acceso privilegiado.

## Acciones a tomar

1. Marcos aplicará los parches críticos en los dos servidores pendientes | fecha: 2026-11-13
2. Acción: Liberar espacio y reprogramar el respaldo nocturno de la base de producción
   Responsable: Marco
   Fecha: mañana
   Estado: en curso
3. Acción: Activar MFA en las 4 cuentas de administrador detectadas | responsable: Carlos | fecha: 2026-11-16
4. Acción: Preparar la evidencia de la revisión de accesos para la auditoría externa | responsable: Elena | plazo: 27/11/2026 | prioridad: alta
5. Acción: Redactar la excepción de acceso privilegiado para el proveedor de monitoreo | responsable: SL | fecha: próximo lunes
6. TODO: Revisar la política de rotación de claves de servicio

## Puntos abiertos

- Pregunta: ¿Quién aprueba las excepciones si el CISO está de vacaciones?
- ¿Se necesita un segundo almacenamiento para los respaldos fuera de la región?
- Por definir: frecuencia del escaneo de vulnerabilidades en ambiente de pruebas
