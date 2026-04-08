# -*- coding: utf-8 -*-
{
    'name': 'BBrands - Cobranza Ticket Management',
    'version': '18.0.1.0.0',
    'summary': 'Gestión automatizada de tickets de cobranza para facturas vencidas',
    'description': """
        Módulo de extensión para gestión de tickets de cobranza.
        - Crea tickets automáticamente para facturas/boletas vencidas hace 10 días
        - Vista dedicada "Boletas vencidas" con hipervínculos a las facturas
        - Campo Many2many para vincular account.move al ticket
        - Cron job configurable para ejecución diaria
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
