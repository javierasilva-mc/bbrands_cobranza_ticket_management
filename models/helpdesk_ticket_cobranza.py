# -*- coding: utf-8 -*-
import datetime
import logging
from markupsafe import Markup
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

PAYMENT_STATE_LABELS = {
    'not_paid':         'Sin pagar',
    'in_payment':       'En proceso de pago',
    'paid':             'Pagado',
    'partial':          'Pago parcial',
    'reversed':         'Revertido',
    'invoicing_legacy': 'Legado',
}

class HelpdeskTicketCobranza(models.Model):
    _inherit = 'helpdesk.ticket'

    invoice_cobranza_ids = fields.Many2many(
        comodel_name='account.move',
        relation='helpdesk_ticket_invoice_cobranza_rel',
        column1='ticket_id',
        column2='move_id',
        string='Boletas / Facturas vencidas',
        domain=[
            ('move_type', 'in', ['out_invoice', 'out_receipt']),
            ('state', '=', 'posted'),
        ],
    )

    cobranza_total_adeudado = fields.Monetary(
        string='Total adeudado',
        compute='_compute_cobranza_total',
        store=False,
        currency_field='cobranza_currency_id',
    )
    cobranza_currency_id = fields.Many2one(
        'res.currency',
        string='Moneda cobranza',
        compute='_compute_cobranza_total',
        store=False,
    )
    cobranza_cantidad_boletas = fields.Integer(
        string='Cantidad de boletas',
        compute='_compute_cobranza_total',
        store=False,
    )

    cobranza_historial_ids = fields.One2many(
        'cobranza.historial',
        'ticket_id',
        string='Historial de cobranza',
        readonly=True,
    )
    
    es_ticket_cobranza = fields.Boolean(
        string='Es ticket de cobranza',
        default=False,
        copy=False,
    )
    
    cobranza_config_id = fields.Many2one(
        'cobranza.config',
        string='Configuración de cobranza',
        ondelete='set null',
    )

    @api.depends('invoice_cobranza_ids', 'invoice_cobranza_ids.amount_residual', 'invoice_cobranza_ids.payment_state')
    def _compute_cobranza_total(self):
        for ticket in self:
            moves = ticket.invoice_cobranza_ids
            pendientes = moves.filtered(
                lambda m: m.payment_state in self._get_estados_pendientes()
            )
            ticket.cobranza_cantidad_boletas = len(pendientes)
            if pendientes:
                ticket.cobranza_total_adeudado = sum(pendientes.mapped('amount_residual'))
                ticket.cobranza_currency_id = pendientes[0].currency_id
            else:
                ticket.cobranza_total_adeudado = 0.0
                ticket.cobranza_currency_id = self.env.company.currency_id

    def _get_cobranza_config(self):
        return self.env['cobranza.config'].get_config()

    def action_ver_boletas_vencidas(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Boletas vencidas',
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.invoice_cobranza_ids.ids)],
            'context': {'default_move_type': 'out_invoice'},
            'target': 'current',
        }

    # -------------------------------------------------------------------------
    # Cron principal
    # -------------------------------------------------------------------------
    @api.model
    def cron_crear_tickets_cobranza(self):
        cfg_model = self.env['cobranza.config']
        todas_configs = cfg_model.search([], order='secuencia')
        if not todas_configs:
            todas_configs = [cfg_model._crear_config_default()]

        hoy = datetime.date.today()

        # Paso 1: actualizar tickets abiertos existentes
        tickets_abiertos = self.env['helpdesk.ticket'].search([
            ('es_ticket_cobranza', '=', True),
            ('state_id', 'not in', [c.stage_cerrado_id.id for c in todas_configs if c.stage_cerrado_id]),
        ])
        partners_con_ticket_abierto = set()
        for ticket in tickets_abiertos:
            partners_con_ticket_abierto.add(ticket.partner_id.id)
            cfg = cfg_model.get_config(partner=ticket.partner_id)
            if not cfg or cfg.partner_excluido(ticket.partner_id):
                continue
            self._actualizar_ticket_existente(ticket, cfg)

        # Paso 2: recopilar partners con ticket cerrado que aún tienen pendientes
        tickets_cerrados = self.env['helpdesk.ticket'].search([
            ('es_ticket_cobranza', '=', True),
            ('state_id', 'in', [c.stage_cerrado_id.id for c in todas_configs if c.stage_cerrado_id]),
        ])
        partners_con_ticket_cerrado = set()
        for ticket in tickets_cerrados:
            cfg = cfg_model.get_config(partner=ticket.partner_id)
            tiene_pendientes = ticket.invoice_cobranza_ids.filtered(
                lambda m: m.payment_state in self._get_estados_pendientes(cfg)
            )
            if tiene_pendientes:
                partners_con_ticket_cerrado.add(ticket.partner_id.id)

        todos_los_partners = partners_con_ticket_abierto | partners_con_ticket_cerrado

        # Paso 3: buscar boletas nuevas y agrupar por (partner, config)
        boletas_nuevas = self.env['account.move'].search([
            ('move_type', 'in', ['out_invoice', 'out_receipt']),
            ('state', '=', 'posted'),
            ('invoice_date', '<=', str(hoy - datetime.timedelta(days=1))),
            ('partner_id', 'not in', list(todos_los_partners)),
            ('partner_id', '!=', False),
        ])

        # Agrupar por (partner_id, config_id)
        grupos = {}
        for move in boletas_nuevas:
            partner = move.partner_id

            # Contar boletas pendientes del cliente para la condición de rango
            num_docs = self.env['account.move'].search_count([
                ('move_type', 'in', ['out_invoice', 'out_receipt']),
                ('payment_state', 'in', ['not_paid', 'partial']),
                ('state', '=', 'posted'),
                ('partner_id', '=', partner.id),
            ])

            cfg = cfg_model.get_config(
                partner=partner,
                move=move,
                num_documentos=num_docs,
            )
            
            if not cfg or cfg.partner_excluido(partner):
                continue
            
            estados = self._get_estados_pendientes(cfg)
            if move.payment_state not in estados:
                continue
            if move.invoice_date > hoy - datetime.timedelta(days=cfg.dias_vencimiento):
                continue
            key = (partner.id, cfg.id)
            if key not in grupos:
                grupos[key] = {
                    'partner': partner,
                    'cfg': cfg,
                    'moves': [],
                }
            grupos[key]['moves'].append(move)

        _logger.info("COBRANZA CRON - Grupos a procesar: %s",
            [(g['partner'].name, g['cfg'].name) for g in grupos.values()])

        for key, data in grupos.items():
            self._crear_ticket_cobranza(data['partner'], data['moves'], data['cfg'])

        # Paso 4: tareas de seguimiento
        for cfg in todas_configs:
            self._cron_crear_tareas_seguimiento(cfg)

    def _actualizar_ticket_existente(self, ticket, cfg=None):
        """Agrega facturas pendientes nuevas al ticket y loguea en chatter."""
        if cfg is None:
            cfg = self.env['cobranza.config'].get_config(partner=ticket.partner_id)

        dominio = [
            ('move_type', 'in', ['out_invoice', 'out_receipt']),
            ('payment_state', 'in', self._get_estados_pendientes(cfg)),
            ('state', '=', 'posted'),
            ('partner_id', '=', ticket.partner_id.id),
        ]
        if cfg.document_type_ids:
            dominio.append(
                ('l10n_latam_document_type_id', 'in', cfg.document_type_ids.ids)
            )
            
        todas = self.env['account.move'].search(dominio)

        ids_existentes = set(ticket.invoice_cobranza_ids.ids)
        nuevas = todas.filtered(lambda m: m.id not in ids_existentes)

        if not nuevas:
            return

        ticket.write({
            'invoice_cobranza_ids': [(4, m.id) for m in nuevas],
        })

        filas = Markup('').join([
            Markup(
                '<tr>'
                '<td><b>{nombre}</b></td>'
                '<td>{monto:,.0f} {moneda}</td>'
                '<td>{emision}</td>'
                '<td>{vencimiento}</td>'
                '</tr>'
            ).format(
                nombre=m.name,
                monto=m.amount_residual,
                moneda=m.currency_id.name,
                emision=str(m.invoice_date or '—'),
                vencimiento=str(m.invoice_date_due or '—'),
            )
            for m in nuevas
        ])

        body = Markup(
            '<p><b>Se agregaron {n} boleta(s)/factura(s) al ticket:</b></p>'
            '<table border="1" cellpadding="4" style="border-collapse:collapse">'
            '<tr><th>Número</th><th>Monto pendiente</th><th>Emisión</th><th>Vencimiento</th></tr>'
            '{filas}'
            '</table>'
        ).format(n=len(nuevas), filas=filas)

        ticket.message_post(body=body, subtype_xmlid='mail.mt_note')

        for m in nuevas:
            self.env['cobranza.historial'].create({
                'ticket_id': ticket.id,
                'move_id': m.id,
                'tipo_evento': 'boleta_agregada',
                'partner_id': ticket.partner_id.id,
                'monto_original': m.amount_total,
                'monto_recuperado': m.amount_residual,
                'currency_id': m.currency_id.id,
                'ejecutivo_ids': [(6, 0, ticket.assigned_to_ids.ids)],
                'fecha_evento': fields.Datetime.now(),
            })

    def _crear_ticket_cobranza(self, partner, moves, cfg=None):
        if cfg is None:
            cfg = self.env['cobranza.config'].get_config(partner=partner)

        # Buscar todas las boletas pendientes del cliente que correspondan a esta config
        dominio = [
            ('move_type', 'in', ['out_invoice', 'out_receipt']),
            ('payment_state', 'in', self._get_estados_pendientes(cfg)),
            ('state', '=', 'posted'),
            ('partner_id', '=', partner.id),
            ('partner_id', '!=', False),
        ]
        if cfg.document_type_ids:
            dominio.append(
                ('l10n_latam_document_type_id', 'in', cfg.document_type_ids.ids)
            )

        todas_pendientes = self.env['account.move'].search(dominio)
        move_ids = todas_pendientes.ids
        detalle = '\n'.join([
            f'- {m.name} | {m.amount_residual:,.0f} {m.currency_id.name} '
            f'| Emisión: {m.invoice_date} | Vence: {m.invoice_date_due or "—"}'
            for m in todas_pendientes
        ])
        total = sum(m.amount_residual for m in todas_pendientes)
        currency = todas_pendientes[0].currency_id.name if todas_pendientes else 'CLP'

        _logger.info("COBRANZA - Creando ticket para partner: %s (id: %s), config: %s, boletas: %s",
            partner.name, partner.id, cfg.name, [m.name for m in todas_pendientes])

        ticket = self.env['helpdesk.ticket'].create({
            'name': f'Cobranza vencida: {partner.name}',
            'es_ticket_cobranza': True,
            'cobranza_config_id': cfg.id,
            'partner_id': partner.id,
            'ticket_type_id': cfg.ticket_type_id.id if cfg.ticket_type_id else False,
            'category_id': cfg.category_id.id if cfg.category_id else False,
            'subcategory_id': cfg.subcategory_id.id if cfg.subcategory_id else False,
            'team_id': cfg.team_id.id if cfg.team_id else False,
            'assigned_to_ids': [(4, cfg.ejecutivo_default_id.id)] if cfg.ejecutivo_default_id else [],
            'invoice_cobranza_ids': [(6, 0, move_ids)],
            'description': (
                f'Cliente: {partner.name}\n'
                f'Total adeudado: {total:,.0f} {currency}\n\n'
                f'Boletas:\n{detalle}'
            ),
        })

        _logger.info("COBRANZA - Ticket creado: id=%s, partner_id=%s",
            ticket.id, ticket.partner_id.id)

        self._crear_tareas_iniciales_cobranza(ticket, cfg)

        self.env['cobranza.historial'].create({
            'ticket_id': ticket.id,
            'tipo_evento': 'ticket_creado',
            'partner_id': partner.id,
            'ejecutivo_ids': [(6, 0, ticket.assigned_to_ids.ids)],
            'fecha_evento': fields.Datetime.now(),
        })
        return ticket

    def _crear_tareas_iniciales_cobranza(self, ticket, cfg=None):
        if cfg is None:
            cfg = self._get_cobranza_config()

        asignado = ticket.assigned_to_ids[:1]
        for tarea_cfg in cfg.tarea_ids.sorted('secuencia'):
            self.env['project.task'].create({
                'ticket_id': ticket.id,
                'task_template_id': tarea_cfg.task_template_id.id,
                'assigned_to_ids': [(4, asignado.id)] if asignado else [],
            })

    def _cron_crear_tareas_seguimiento(self, cfg=None):
        if cfg is None:
            cfg = self._get_cobranza_config()

        hoy = datetime.date.today()

        if not cfg.regla_ids:
            return

        tickets_abiertos = self.env['helpdesk.ticket'].search([
            ('es_ticket_cobranza', '=', True),
            ('cobranza_config_id', '=', cfg.id),
            ('state_id', '!=', cfg.stage_cerrado_id.id),
        ])

        for ticket in tickets_abiertos:
            tiene_pendientes = ticket.invoice_cobranza_ids.filtered(
                lambda m: m.payment_state in self._get_estados_pendientes(cfg)
            )
            if not tiene_pendientes:
                continue

            tareas_creadas = []

            for regla in cfg.regla_ids.sorted('secuencia'):
                if regla.condicion == 'fecha_vencida':
                    tarea_disparadora = self.env['project.task'].search([
                        ('ticket_id', '=', ticket.id),
                        ('task_template_id', '=', regla.tarea_disparadora_id.id),
                        ('cobranza_fecha_acuerdo', '=', str(hoy)),
                    ], limit=1)

                    if not tarea_disparadora:
                        continue

                elif regla.condicion == 'dias_ultima_tarea':
                    dominio = [
                        ('ticket_id', '=', ticket.id),
                        ('task_template_id', '=', regla.tarea_disparadora_id.id),
                    ]
                    if regla.requiere_tarea_completada:
                        stage_done = cfg.stage_tarea_completada_id
                        if not stage_done:
                            continue
                        dominio.append(('state_id', '=', stage_done.id))

                    tarea_disparadora = self.env['project.task'].search(
                        dominio, limit=1)

                    if not tarea_disparadora:
                        continue

                    if regla.requiere_tarea_completada:
                        fecha_referencia = tarea_disparadora.date_last_stage_update
                    else:
                        fecha_referencia = tarea_disparadora.create_date

                    if not fecha_referencia:
                        continue

                    dias_transcurridos = (hoy - fecha_referencia.date()).days
                    if dias_transcurridos < regla.dias_condicion:
                        continue

                else:
                    continue

                for tarea_cfg in regla.tarea_ids.sorted('secuencia'):
                    ya_existe = self.env['project.task'].search([
                        ('ticket_id', '=', ticket.id),
                        ('task_template_id', '=', tarea_cfg.task_template_id.id),
                    ], limit=1)

                    if ya_existe:
                        continue

                    asignado = ticket.assigned_to_ids[:1]
                    self.env['project.task'].create({
                        'ticket_id': ticket.id,
                        'task_template_id': tarea_cfg.task_template_id.id,
                        'planned_date': hoy,
                        'assigned_to_ids': [(4, asignado.id)] if asignado else [],
                    })
                    tareas_creadas.append(tarea_cfg.task_template_id.name)

            # Un solo mensaje por ticket con todas las tareas creadas
            if tareas_creadas:
                ticket.message_post(
                    body=Markup(
                        '<p><b>Tarea(s) de seguimiento creadas</b> — '
                        'el cliente no regularizó al {fecha}.</p>'
                    ).format(fecha=str(hoy)),
                    subtype_xmlid='mail.mt_note',
                )

                self.env['cobranza.historial'].create({
                    'ticket_id': ticket.id,
                    'tipo_evento': 'seguimiento',
                    'partner_id': ticket.partner_id.id,
                    'ejecutivo_ids': [(6, 0, ticket.assigned_to_ids.ids)],
                    'fecha_evento': fields.Datetime.now(),
                })
                
    def write(self, vals):
        # Capturar tickets que van a cerrarse
        tickets_a_cerrar = []
        if 'state_id' in vals and not self.env.context.get('skip_cobranza_check'):
            for ticket in self:
                if not ticket.es_ticket_cobranza:
                    continue
                cfg = ticket.cobranza_config_id
                if not cfg:
                    continue
                if vals['state_id'] == cfg.stage_cerrado_id.id:
                    pendientes = ticket.invoice_cobranza_ids.filtered(
                        lambda m: m.payment_state in self._get_estados_pendientes(cfg)
                    )
                    if pendientes:
                        raise UserError(_(
                            'No puedes cerrar el ticket "%s" mientras existen '
                            'boletas pendientes de pago.'
                        ) % ticket.name)
                    tickets_a_cerrar.append(ticket)

        res = super().write(vals)

        # Registrar cierre en historial
        for ticket in tickets_a_cerrar:
            self.env['cobranza.historial'].create({
                'ticket_id': ticket.id,
                'tipo_evento': 'ticket_cerrado',
                'partner_id': ticket.partner_id.id,
                'ejecutivo_ids': [(6, 0, ticket.assigned_to_ids.ids)],
                'fecha_evento': fields.Datetime.now(),
            })

        return res
    
    def _get_estados_pendientes(self, cfg=None):
        if cfg is None:
            cfg = self._get_cobranza_config()
        estados = ['not_paid', 'partial']
        if cfg.incluir_in_payment:
            estados.append('in_payment')
        return estados


class AccountMoveCobranza(models.Model):
    _inherit = 'account.move'

    def _compute_payment_state(self):
        estados_anteriores = {move.id: move.payment_state for move in self}
        super()._compute_payment_state()

        ticket_model = self.env['helpdesk.ticket']

        for move in self:
            estado_anterior = estados_anteriores.get(move.id)
            estado_nuevo = move.payment_state

            if not estado_anterior or estado_anterior == estado_nuevo:
                continue

            # Buscar tickets abiertos — cada ticket tiene su propia config
            tickets_abiertos = ticket_model.search([
                ('invoice_cobranza_ids', 'in', move.id),
                ('es_ticket_cobranza', '=', True),
            ]).filtered(lambda t: (
                t.cobranza_config_id and
                t.state_id.id != t.cobranza_config_id.stage_cerrado_id.id
            ))

            label_ant = PAYMENT_STATE_LABELS.get(estado_anterior, estado_anterior)
            label_nvo = PAYMENT_STATE_LABELS.get(estado_nuevo, estado_nuevo)

            for ticket in tickets_abiertos:
                cfg = ticket.cobranza_config_id
                estados_pendientes = self.env['helpdesk.ticket']._get_estados_pendientes(cfg)

                asignados = ', '.join(ticket.assigned_to_ids.mapped('name')) if ticket.assigned_to_ids else 'Sin asignar'
                body = Markup(
                    '<p><b>Cambio de estado en documento:</b></p>'
                    '<table border="1" cellpadding="4" style="border-collapse:collapse">'
                    '<tr><th>Documento</th><th>Estado anterior</th><th>Estado nuevo</th><th>Monto pendiente</th><th>Asignado a</th></tr>'
                    '<tr>'
                    '<td><b>{nombre}</b></td>'
                    '<td>{ant}</td>'
                    '<td><b>{nvo}</b></td>'
                    '<td>{monto:,.0f} {moneda}</td>'
                    '<td>{asignados}</td>'
                    '</tr>'
                    '</table>'
                ).format(
                    nombre=move.name,
                    ant=label_ant,
                    nvo=label_nvo,
                    monto=move.amount_residual,
                    moneda=move.currency_id.name,
                    asignados=asignados,
                )
                ticket.message_post(body=body, subtype_xmlid='mail.mt_note')

                self.env['cobranza.historial'].create({
                    'ticket_id': ticket.id,
                    'move_id': move.id,
                    'tipo_evento': 'pago',
                    'estado_anterior': estado_anterior,
                    'estado_nuevo': estado_nuevo,
                    'partner_id': ticket.partner_id.id,
                    'monto_original': move.amount_total,
                    'monto_recuperado': move.amount_total - move.amount_residual,
                    'currency_id': move.currency_id.id,
                    'ejecutivo_ids': [(6, 0, ticket.assigned_to_ids.ids)],
                    'fecha_evento': fields.Datetime.now(),
                })

            # Buscar tickets CERRADOS para reabrir
            tickets_cerrados = ticket_model.search([
                ('invoice_cobranza_ids', 'in', move.id),
                ('es_ticket_cobranza', '=', True),
            ]).filtered(lambda t: (
                t.cobranza_config_id and
                t.state_id.id == t.cobranza_config_id.stage_cerrado_id.id
            ))

            for ticket in tickets_cerrados:
                cfg = ticket.cobranza_config_id
                estados_pendientes = self.env['helpdesk.ticket']._get_estados_pendientes(cfg)

                if estado_nuevo not in estados_pendientes:
                    continue

                ticket.with_context(skip_cobranza_check=True).write({
                    'state_id': cfg.stage_in_progress_id.id,
                    'is_closed': False,
                })
                ticket.message_post(
                    body=Markup(
                        '<p><b>Ticket reabierto</b> — la boleta <b>{nombre}</b> '
                        'volvió a estado pendiente ({estado}).</p>'
                    ).format(
                        nombre=move.name,
                        estado=label_nvo,
                    ),
                    subtype_xmlid='mail.mt_note',
                )
                self.env['cobranza.historial'].create({
                    'ticket_id': ticket.id,
                    'move_id': move.id,
                    'tipo_evento': 'reapertura',
                    'estado_anterior': estado_anterior,
                    'estado_nuevo': estado_nuevo,
                    'partner_id': ticket.partner_id.id,
                    'monto_original': move.amount_total,
                    'monto_recuperado': 0.0,
                    'currency_id': move.currency_id.id,
                    'ejecutivo_ids': [(6, 0, ticket.assigned_to_ids.ids)],
                    'fecha_evento': fields.Datetime.now(),
                })


class ProjectTaskCobranza(models.Model):
    _inherit = 'project.task'

    cobranza_fecha_acuerdo = fields.Date(
        string='Fecha de compromiso de pago',
    )

    def write(self, vals):
        fechas_anteriores = {}
        if 'cobranza_fecha_acuerdo' in vals:
            for task in self:
                fechas_anteriores[task.id] = task.cobranza_fecha_acuerdo

        if 'state_id' in vals:
            for task in self:
                if not task.ticket_id or not task.ticket_id.es_ticket_cobranza:
                    continue
                cfg = task.ticket_id.cobranza_config_id
                if not cfg:
                    continue
                stage_done = cfg.stage_tarea_completada_id
                if not stage_done or vals['state_id'] != stage_done.id:
                    continue
                tarea_acuerdo_cfg = cfg.tarea_ids.filtered(lambda t: t.es_tarea_acuerdo)
                if not tarea_acuerdo_cfg:
                    continue
                tmpl_id = tarea_acuerdo_cfg[0].task_template_id.id
                if (task.task_template_id.id == tmpl_id
                        and not task.cobranza_fecha_acuerdo):
                    raise UserError(_(
                        'No puedes cerrar la tarea "%s" sin registrar '
                        'una fecha de compromiso de pago.'
                    ) % task.name)

        res = super().write(vals)

        if fechas_anteriores:
            for task in self:
                fecha_anterior = fechas_anteriores.get(task.id)
                fecha_nueva = task.cobranza_fecha_acuerdo
                if fecha_nueva and fecha_anterior != fecha_nueva and task.ticket_id:
                    self.env['cobranza.historial'].create({
                        'ticket_id': task.ticket_id.id,
                        'tipo_evento': 'acuerdo',
                        'partner_id': task.ticket_id.partner_id.id,
                        'fecha_acuerdo': fecha_nueva,
                        'ejecutivo_ids': [(6, 0, task.ticket_id.assigned_to_ids.ids)],
                        'fecha_evento': fields.Datetime.now(),
                    })

        return res

    def _compute_domain_worksheet_template_ids(self):
        for rec in self:
            allowed = rec.env['worksheet.template']
            if rec.task_template_id:
                allowed |= rec.task_template_id.successful_worksheet_template_ids
                allowed |= rec.task_template_id.failure_worksheet_template_ids
            base_domain = [
                ('res_model', '=', 'project.task'),
                '|', ('company_id', '=', False), ('company_id', '=', rec.company_id.id)
            ]
            rec.domain_worksheet_template_ids = allowed.filtered_domain(base_domain)
            
    def _compute_allowed_lots(self):
        StockQuant = self.env['stock.quant']
        for record in self:
            if not record.changeperiodisity or not record.changeholder_verify or not record.changeofaddress:
                if not record.subscription.main_product:
                    record.allowed_lot_ids = False
                    continue
                user_location = record._get_user_valid_location()
                if not user_location:
                    record.allowed_lot_ids = False
                    continue
                allowed_stage_codes = user_location.product_stage_ids.mapped('code')
                if not allowed_stage_codes:
                    record.allowed_lot_ids = False
                    continue
                quants = StockQuant.search([
                    ('location_id', '=', user_location.id),
                    ('quantity', '>', 0),
                    ('reserved_quantity', '=', 0),
                    ('product_id', '=', record.subscription.main_product.id),
                    ('lot_id', '!=', False),
                ])
                valid_lots = quants.mapped('lot_id').filtered(
                    lambda lot: lot.state in allowed_stage_codes)
                record.allowed_lot_ids = [(6, 0, valid_lots.ids)]
            else:
                record.allowed_lot_ids = False


class CobranzaHistorial(models.Model):
    _name = 'cobranza.historial'
    _description = 'Historial de eventos de cobranza'
    _order = 'fecha_evento desc'

    ticket_id = fields.Many2one(
        'helpdesk.ticket',
        string='Ticket',
        required=True,
        ondelete='cascade',
        index=True,
    )
    move_id = fields.Many2one(
        'account.move',
        string='Boleta / Factura',
        ondelete='set null',
        index=True,
    )
    tipo_evento = fields.Selection([
        ('ticket_creado',   'Ticket creado'),
        ('boleta_agregada', 'Boleta agregada'),
        ('pago',            'Cambio de estado de pago'),
        ('acuerdo',         'Acuerdo de pago registrado'),
        ('seguimiento',     'Seguimiento creado'),
        ('reapertura',      'Ticket reabierto'),
        ('ticket_cerrado',  'Ticket cerrado'),
    ], string='Tipo de evento', required=True)
    estado_anterior = fields.Char(string='Estado anterior')
    estado_nuevo    = fields.Char(string='Estado nuevo')
    monto_original  = fields.Monetary(
        string='Monto original',
        currency_field='currency_id',
    )
    monto_recuperado = fields.Monetary(
        string='Monto recuperado',
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Moneda',
        default=lambda self: self.env.company.currency_id,
    )
    ejecutivo_ids = fields.Many2many(
        'res.users',
        'cobranza_historial_ejecutivo_rel',
        'historial_id',
        'user_id',
        string='Ejecutivos asignados',
    )
    partner_id  = fields.Many2one('res.partner', string='Cliente', index=True)
    fecha_evento = fields.Datetime(
        string='Fecha evento',
        default=fields.Datetime.now,
        required=True,
    )
    fecha_acuerdo = fields.Date(string='Fecha de acuerdo')