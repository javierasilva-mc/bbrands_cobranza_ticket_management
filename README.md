# bbrands_cobranza_ticket_management

Módulo Odoo 18 desarrollado por **BBrands** para **Maihuechile**. Extiende `pragtech_ticket_management` para automatizar la gestión de tickets de cobranza a partir de facturas y boletas vencidas.

---

## Descripción general

Este módulo crea y gestiona tickets de cobranza de forma automática cuando un cliente tiene documentos tributarios vencidos, registra cada evento relevante en el chatter del ticket y mantiene un historial de cobranza auditable. Toda la lógica de automatización se ejecuta vía cron job diario y es configurable desde un panel único dentro del módulo de Helpdesk.

---

## Dependencias

| Módulo | Origen |
|---|---|
| `pragtech_ticket_management` | Proveedor externo (debe estar instalado previamente) |
| `account` | Odoo core |
| `helpdesk` | Odoo Enterprise |
| `industry_fsm` | Odoo Enterprise |

---

## Funcionalidades

### Creación automática de tickets
El cron job diario busca facturas y boletas (`out_invoice`, `out_receipt`) en estado `posted` con pago pendiente (`not_paid`, `partial`) cuya fecha de emisión supera los días de vencimiento configurados. Crea un ticket por cliente agrupando todos sus documentos pendientes. Si el cliente ya tiene un ticket abierto, agrega los documentos nuevos al ticket existente.

### Vinculación de facturas (Many2many)
Cada ticket expone un campo `invoice_cobranza_ids` que vincula los `account.move` pendientes del cliente. Incluye totales calculados: monto adeudado, moneda y cantidad de boletas.

### Chatter automático de cambios de estado de pago
Al detectar un cambio en `payment_state` de cualquier `account.move` vinculado a un ticket, se registra automáticamente una nota en el chatter del ticket con el estado anterior, el estado nuevo, el monto pendiente y los ejecutivos asignados.

### Reapertura automática de tickets
Si una boleta vuelve a estado pendiente después de que su ticket fue cerrado (por ejemplo, por reversa de pago), el ticket se reabre automáticamente al estado configurado como "en progreso" y se registra el evento en el chatter.

### Bloqueo de cierre con deuda pendiente
No es posible cerrar un ticket de cobranza mientras existan boletas con pago pendiente vinculadas. El sistema lanza un error informativo al intentarlo.

### Tareas iniciales por plantilla
Al crear un ticket, el cron genera automáticamente las tareas iniciales definidas en la configuración (p. ej. "Contacto inicial", "Acuerdo de pago"), asignadas al ejecutivo por defecto.

### Reglas de seguimiento automático
La configuración permite definir reglas que crean tareas de seguimiento condicionadas a:
- **Fecha de acuerdo vencida**: se dispara cuando vence la fecha de compromiso de pago registrada en una tarea específica.
- **Días desde tarea completada**: se dispara N días después de que una tarea disparadora fue marcada como completada.

### Historial de cobranza (`cobranza.historial`)
Todos los eventos relevantes quedan registrados en el modelo `cobranza.historial`, incluyendo: creación de ticket, boletas agregadas, cambios de estado de pago, acuerdos de pago, seguimientos y reaperturas.

### Singleton de configuración
Panel accesible desde el menú de Helpdesk que centraliza toda la configuración del módulo: días de vencimiento, tipo/categoría/equipo de ticket, ejecutivo por defecto, stages, tareas iniciales y reglas de seguimiento.

---

## Estructura del módulo

```
bbrands_cobranza_ticket_management/
├── __init__.py
├── __manifest__.py
├── data/
│   └── cron_cobranza.xml          # Cron job diario
├── models/
│   ├── __init__.py
│   ├── cobranza_config.py         # Singleton de configuración + modelos de reglas y tareas
│   └── helpdesk_ticket_cobranza.py # Extensiones de helpdesk.ticket, account.move, project.task y cobranza.historial
├── security/
│   └── ir.model.access.csv        # Permisos de acceso
└── views/
    ├── cobranza_config_view.xml    # Vista del panel de configuración
    └── helpdesk_ticket_cobranza_view.xml  # Vistas de tickets de cobranza
```

---

## Instalación

> **Prerequisito:** El módulo `pragtech_ticket_management` debe estar instalado en el ambiente Odoo destino antes de instalar este módulo.

1. Copiar la carpeta `bbrands_cobranza_ticket_management` al directorio de addons del servidor Odoo.
2. Reiniciar el servidor Odoo.
3. Activar el modo desarrollador en Odoo.
4. Ir a **Aplicaciones → Actualizar lista de módulos**.
5. Buscar `BBrands - Cobranza Ticket Management` e instalar.

---

## Configuración inicial

Tras la instalación, acceder al panel de configuración desde:

**Helpdesk → Configuración → Configuración de Cobranza**

Configurar como mínimo:

| Campo | Descripción |
|---|---|
| Días de vencimiento | Días desde la fecha de emisión para activar la cobranza (por defecto: 10) |
| Stage cerrado | Stage del ticket que representa el cierre |
| Stage reapertura | Stage al que se mueve el ticket al reabrir |
| Stage tarea completada | Stage de `project.task` que representa tarea finalizada |
| Tipo / Categoría / Subcategoría | Clasificación de los tickets generados |
| Equipo | Equipo de Helpdesk asignado |
| Ejecutivo por defecto | Usuario asignado por defecto a los tickets y tareas |
| Tareas iniciales | Plantillas de tareas a crear al abrir un ticket |
| Reglas de seguimiento | Condiciones para generar tareas de seguimiento automático |


