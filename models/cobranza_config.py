# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError


class CobranzaConfig(models.Model):
    _name = 'cobranza.config'
    _description = 'Configuración del módulo de cobranza'
    _order = 'secuencia'

    name = fields.Char(
        string='Nombre',
        required=True,
    )
    secuencia = fields.Integer(
        string='Secuencia',
        default=10,
        help='Cuando un cliente/documento coincide con múltiples configuraciones, '
             'se aplica la de menor secuencia.',
    )
    es_default = fields.Boolean(
        string='Configuración por defecto',
        default=False,
        help='Se aplica cuando ninguna otra configuración coincide con el cliente y documento.',
    )

    # Condiciones
    business_unit_ids = fields.Many2many(
        'business.unit',
        'cobranza_config_business_unit_rel',
        'config_id',
        'business_unit_id',
        string='Unidades de negocio',
        required=True,
    )
    document_type_ids = fields.Many2many(
        'l10n_latam.document.type',
        'cobranza_config_document_type_rel',
        'config_id',
        'document_type_id',
        string='Tipos de documento',
        required=True,
    )
    
    payment_term_ids = fields.Many2many(
        'account.payment.term',
        'cobranza_config_payment_term_rel',
        'config_id',
        'payment_term_id',
        string='Plazos de pago',
        help='Si se configura, aplica solo a clientes que tengan algún contrato con este plazo. '
            'Vacío aplica a cualquier plazo.',
    )
    min_documentos = fields.Integer(
        string='Mínimo de documentos',
        default=0,
        help='Cantidad mínima de boletas/facturas pendientes. 0 indica sin límite inferior.',
    )
    max_documentos = fields.Integer(
        string='Máximo de documentos',
        default=0,
        help='Cantidad máxima de boletas/facturas pendientes. 0 indica sin límite superior.',
    )

    # Configuración general
    dias_vencimiento = fields.Integer(
        string='Días de vencimiento',
        default=10,
    )
    incluir_in_payment = fields.Boolean(
        string='Incluir "En proceso de pago" como pendiente',
        default=False,
    )
    ticket_type_id = fields.Many2one('ticket.type', string='Tipo de ticket')
    category_id = fields.Many2one('ticket.category', string='Categoría')
    subcategory_id = fields.Many2one('ticket.subcategory', string='Subcategoría')
    subcategory_template_id = fields.Many2one('subcategory.template', string='Plantilla de subcategoría')
    stage_cerrado_id = fields.Many2one('helpdesk.ticket.stage', string='Stage cerrado')
    stage_in_progress_id = fields.Many2one('helpdesk.ticket.stage', string='Stage reapertura')
    stage_tarea_completada_id = fields.Many2one('project.task.stage', string='Stage tarea completada')
    team_id = fields.Many2one('helpdesk.team', string='Equipo')
    ejecutivo_default_id = fields.Many2one('res.users', string='Ejecutivo por defecto')

    # Tareas iniciales
    tarea_ids = fields.One2many(
        'cobranza.config.tarea',
        'config_id',
        string='Tareas iniciales',
    )

    # Reglas de seguimiento
    regla_ids = fields.One2many(
        'cobranza.config.regla',
        'config_id',
        string='Reglas de seguimiento',
    )
    
    state = fields.Selection([
        ('borrador', 'Borrador'),
        ('activo',   'Activo'),
        ('inactivo', 'Inactivo'),
    ], string='Estado', default='borrador', required=True)
    
    segmento_excepcion_id = fields.Many2one(
        'customer.segment',
        string='Segmento de excepción',
        help='Los clientes con este segmento serán excluidos del proceso de cobranza para esta configuración.',
    )
    
    def action_activar(self):
        self.ensure_one()
        # Validar campos obligatorios antes de activar
        campos_faltantes = []
        if not self.ticket_type_id:
            campos_faltantes.append('Tipo de ticket')
        if not self.category_id:
            campos_faltantes.append('Categoría')
        if not self.subcategory_id:
            campos_faltantes.append('Subcategoría')
        if not self.stage_cerrado_id:
            campos_faltantes.append('Stage cerrado')
        if not self.stage_in_progress_id:
            campos_faltantes.append('Stage reapertura')
        if not self.stage_tarea_completada_id:
            campos_faltantes.append('Stage tarea completada')
        if not self.team_id:
            campos_faltantes.append('Equipo')

        if campos_faltantes:
            raise UserError(
                'No puedes activar esta configuración. '
                'Faltan los siguientes campos obligatorios:\n- '
                + '\n- '.join(campos_faltantes)
            )

        self._check_condiciones_duplicadas()
        self.state = 'activo'

    def action_desactivar(self):
        self.ensure_one()
        self.state = 'inactivo'

    def action_reactivar(self):
        self.ensure_one()
        self._check_condiciones_duplicadas()
        self.state = 'activo'
    
    def write(self, vals):
        for rec in self:
            tiene_tickets = self.env['helpdesk.ticket'].search_count([
                ('cobranza_config_id', '=', rec.id),
                ('es_ticket_cobranza', '=', True),
            ])
            if tiene_tickets or rec.state == 'activo':
                campos_permitidos = {'name', 'state'}
                campos_no_permitidos = set(vals.keys()) - campos_permitidos
                if campos_no_permitidos:
                    raise UserError(
                        f'La configuración "{rec.name}" no puede ser modificada. '
                        f'Si necesitas cambiar la configuración, por favor crea una nueva.'
                    )
        return super().write(vals)

    @api.constrains('es_default')
    def _check_unico_default(self):
        for rec in self:
            if rec.es_default:
                otras = self.search([
                    ('es_default', '=', True),
                    ('id', '!=', rec.id),
                ])
                if otras:
                    raise UserError(
                        'Solo puede haber una configuración marcada como "Por defecto".'
                    )

    @api.model
    def get_config(self, partner=None, move=None, num_documentos=0):
        todas = self.search([
            ('es_default', '=', False),
            ('state', '=', 'activo'),
        ], order='secuencia')

        if not todas and not self.search([('es_default', '=', True)]):
            return self._crear_config_default()

        if partner or move:
            business_unit_id = (
                partner.business_unit_id.id
                if partner and partner.business_unit_id else False
            )
            document_type_id = (
                move.l10n_latam_document_type_id.id
                if move and move.l10n_latam_document_type_id else False
            )

            payment_term_ids = []
            if partner:
                contratos = self.env['contract.contract'].search([
                    ('client_name', '=', partner.id)
                ])
                payment_term_ids = contratos.mapped('payment_term').ids

            # Separar configs en grupos por especificidad
            configs_con_plazo_y_rango = todas.filtered(
                lambda c: c.payment_term_ids and (c.min_documentos > 0 or c.max_documentos > 0)
            )
            configs_con_plazo = todas.filtered(
                lambda c: c.payment_term_ids and not (c.min_documentos > 0 or c.max_documentos > 0)
            )
            configs_con_rango = todas.filtered(
                lambda c: not c.payment_term_ids and (c.min_documentos > 0 or c.max_documentos > 0)
            )
            configs_generales = todas.filtered(
                lambda c: not c.payment_term_ids and not (c.min_documentos > 0 or c.max_documentos > 0)
            )

            for grupo in [configs_con_plazo_y_rango, configs_con_plazo, configs_con_rango, configs_generales]:
                for cfg in grupo:
                    bu_match = business_unit_id and business_unit_id in cfg.business_unit_ids.ids
                    dt_match = document_type_id and document_type_id in cfg.document_type_ids.ids

                    if not cfg.payment_term_ids:
                        pt_match = True
                    else:
                        pt_match = bool(set(payment_term_ids) & set(cfg.payment_term_ids.ids))

                    if num_documentos > 0:
                        min_ok = (cfg.min_documentos == 0 or num_documentos >= cfg.min_documentos)
                        max_ok = (cfg.max_documentos == 0 or num_documentos <= cfg.max_documentos)
                        rango_match = min_ok and max_ok
                    else:
                        rango_match = True

                    if bu_match and dt_match and pt_match and rango_match:
                        return cfg

        # Fallback a la configuración por defecto
        default = self.search([
            ('es_default', '=', True),
            ('state', '=', 'activo'),
        ], limit=1)
        if default:
            return default

        return False

    def partner_excluido(self, partner):
        """Retorna True si el partner debe ser excluido de esta configuración."""
        if not self.segmento_excepcion_id:
            return False
        return partner.customer_segment_id.id == self.segmento_excepcion_id.id

    def _crear_config_default(self):
        stage_cerrado = self.env['helpdesk.ticket.stage'].search(
            [('name', '=', 'Closed')], limit=1)
        stage_in_progress = self.env['helpdesk.ticket.stage'].search(
            [('name', '=', 'In-progress')], limit=1)
        stage_tarea_completada = self.env['project.task.stage'].search(
            [('name', '=', 'Done')], limit=1)

        config = self.create({
            'name': 'Configuración por defecto',
            'secuencia': 10,
            'es_default': True,
            'dias_vencimiento': 10,
            'stage_cerrado_id': stage_cerrado.id if stage_cerrado else False,
            'stage_in_progress_id': stage_in_progress.id if stage_in_progress else False,
            'stage_tarea_completada_id': stage_tarea_completada.id if stage_tarea_completada else False,
        })

        tmpl_contacto = self.env['task.template'].search(
            [('name', '=', 'Contacto inicial')], limit=1)
        tmpl_acuerdo = self.env['task.template'].search(
            [('name', '=', 'Acuerdo de pago')], limit=1)
        tmpl_seguimiento = self.env['task.template'].search(
            [('name', '=', 'Seguimiento de acuerdo')], limit=1)

        if tmpl_contacto:
            self.env['cobranza.config.tarea'].create({
                'config_id': config.id,
                'secuencia': 1,
                'task_template_id': tmpl_contacto.id,
                'es_tarea_acuerdo': False,
            })
        if tmpl_acuerdo:
            self.env['cobranza.config.tarea'].create({
                'config_id': config.id,
                'secuencia': 2,
                'task_template_id': tmpl_acuerdo.id,
                'es_tarea_acuerdo': True,
            })
        if tmpl_acuerdo and tmpl_seguimiento:
            regla = self.env['cobranza.config.regla'].create({
                'config_id': config.id,
                'secuencia': 1,
                'tarea_disparadora_id': tmpl_acuerdo.id,
                'condicion': 'fecha_vencida',
            })
            self.env['cobranza.config.regla.tarea'].create({
                'regla_id': regla.id,
                'secuencia': 1,
                'task_template_id': tmpl_seguimiento.id,
            })
        return config

    @api.model
    def action_open_config(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Configuración de Cobranza',
            'res_model': 'cobranza.config',
            'view_mode': 'list,form',
            'target': 'current',
        }
        
    @api.constrains(
        'business_unit_ids', 'document_type_ids',
        'payment_term_ids', 'min_documentos', 'max_documentos',
        'state'
    )
    def _check_condiciones_duplicadas(self):
        for rec in self:
            if rec.es_default:
                continue
            otras = self.search([
                ('id', '!=', rec.id),
                ('es_default', '=', False),
                ('state', '=', 'activo'),
            ])
            for otra in otras:
                # Verificar solapamiento en business_unit_ids
                bu_solapa = bool(
                    set(rec.business_unit_ids.ids) & set(otra.business_unit_ids.ids)
                )
                # Verificar solapamiento en document_type_ids
                dt_solapa = bool(
                    set(rec.document_type_ids.ids) & set(otra.document_type_ids.ids)
                )
                # Verificar solapamiento en payment_term_ids
                # Solo solapa si ambas están vacías (aplican a cualquier plazo)
                # o si comparten algún plazo específico
                if not rec.payment_term_ids and not otra.payment_term_ids:
                    pt_solapa = True
                elif not rec.payment_term_ids or not otra.payment_term_ids:
                    # Una tiene plazo y la otra no → no solapa
                    pt_solapa = False
                else:
                    pt_solapa = bool(
                        set(rec.payment_term_ids.ids) & set(otra.payment_term_ids.ids)
                    )
                # Verificar solapamiento en rango de documentos
                rango_solapa = self._rangos_solapan(
                    rec.min_documentos, rec.max_documentos,
                    otra.min_documentos, otra.max_documentos,
                )

                if bu_solapa and dt_solapa and pt_solapa and rango_solapa:
                    raise UserError(
                        f'La configuración "{rec.name}" solapa condiciones con '
                        f'"{otra.name}". Revisa las unidades de negocio, tipos de '
                        f'documento, plazos de pago y rango de documentos.'
                    )

    @api.model
    def _rangos_solapan(self, min1, max1, min2, max2):
        """
        Verifica si dos rangos [min1, max1] y [min2, max2] se solapan.
        0 en min indica sin límite inferior (= 0).
        0 en max indica sin límite superior (= infinito).
        """
        # Convertir 0 en max a infinito
        max1_eff = max1 if max1 > 0 else float('inf')
        max2_eff = max2 if max2 > 0 else float('inf')
        min1_eff = min1 if min1 > 0 else 0
        min2_eff = min2 if min2 > 0 else 0
        # Dos rangos [a,b] y [c,d] solapan si a <= d y c <= b
        return min1_eff <= max2_eff and min2_eff <= max1_eff


class CobranzaConfigTarea(models.Model):
    _name = 'cobranza.config.tarea'
    _description = 'Tarea inicial de cobranza'
    _order = 'secuencia'

    config_id = fields.Many2one('cobranza.config', required=True, ondelete='cascade')
    secuencia = fields.Integer(string='Secuencia', default=10)
    task_template_id = fields.Many2one('task.template', string='Plantilla de tarea', required=True)
    es_tarea_acuerdo = fields.Boolean(
        string='Es tarea de acuerdo',
        help='Marca esta tarea como la que registra la fecha de compromiso de pago. '
             'Solo puede haber una activa.',
    )

    @api.constrains('es_tarea_acuerdo', 'config_id')
    def _check_unica_tarea_acuerdo(self):
        for rec in self:
            if rec.es_tarea_acuerdo:
                otras = self.search([
                    ('config_id', '=', rec.config_id.id),
                    ('es_tarea_acuerdo', '=', True),
                    ('id', '!=', rec.id),
                ])
                if otras:
                    raise UserError(
                        'Solo puede haber una tarea marcada como "Tarea de acuerdo".'
                    )


class CobranzaConfigRegla(models.Model):
    _name = 'cobranza.config.regla'
    _description = 'Regla de seguimiento de cobranza'
    _order = 'secuencia'

    config_id = fields.Many2one('cobranza.config', required=True, ondelete='cascade')
    secuencia = fields.Integer(string='Secuencia', default=10)
    tarea_disparadora_id = fields.Many2one('task.template', string='Tarea disparadora', required=True)
    condicion = fields.Selection([
        ('fecha_vencida',     'Fecha de acuerdo vencida'),
        ('dias_ultima_tarea', 'Días desde tarea completada'),
    ], string='Condición', required=True, default='fecha_vencida')
    dias_condicion = fields.Integer(string='Días', default=3)
    requiere_tarea_completada = fields.Boolean(string='Requiere tarea completada', default=True)
    tarea_ids = fields.One2many('cobranza.config.regla.tarea', 'regla_id', string='Tareas a crear')

    @api.constrains('dias_condicion', 'condicion')
    def _check_dias_condicion(self):
        for rec in self:
            if rec.condicion == 'dias_ultima_tarea' and rec.dias_condicion < 1:
                raise UserError('Los días de la condición deben ser mayor a 0.')

    @api.onchange('dias_condicion', 'condicion')
    def _onchange_dias_condicion(self):
        if self.condicion == 'dias_ultima_tarea' and self.dias_condicion < 1:
            return {
                'warning': {
                    'title': 'Valor inválido',
                    'message': 'Los días de la condición deben ser mayor a 0.',
                }
            }


class CobranzaConfigReglaTarea(models.Model):
    _name = 'cobranza.config.regla.tarea'
    _description = 'Tarea generada por regla de seguimiento'
    _order = 'secuencia'

    regla_id = fields.Many2one('cobranza.config.regla', required=True, ondelete='cascade')
    secuencia = fields.Integer(string='Secuencia', default=10)
    task_template_id = fields.Many2one('task.template', string='Plantilla de tarea', required=True)