# -*- coding: utf-8 -*-
{
    'name': 'BBrands - Cobranza Ticket Management',
    'version': '18.0.1.0.0',
    'summary': 'Gestión automatizada de tickets de cobranza para facturas vencidas',
    'description': """
        Módulo de extensión para gestión automatizada de tickets de cobranza.
        - Múltiples configuraciones con condiciones por unidad de negocio, tipo de documento,
        plazo de pago y rango de documentos pendientes
        - Creación automática diaria de tickets agrupados por configuración
        - Tareas iniciales y reglas de seguimiento configurables
        - Historial de trazabilidad completo de eventos
        - Reapertura automática de tickets al volver a estado pendiente
        - Exclusión de clientes por segmento configurable
    """,
    'author': 'BBrands',
    'category': 'Services/Helpdesk',
    'depends': [
        'pragtech_ticket_management',
        'account',
        'helpdesk',
        'industry_fsm',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/cobranza_config_view.xml',
        'data/cron_cobranza.xml',
        'views/helpdesk_ticket_cobranza_view.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
