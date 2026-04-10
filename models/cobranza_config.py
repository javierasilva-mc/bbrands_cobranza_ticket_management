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
    def get_config(self, partner=None, move=None):
        """
        Retorna la configuración más específica que coincida con el partner y/o move.
        Si no hay coincidencia, retorna la configuración por defecto.
        Si no existe ninguna configuración, crea la por defecto.
        """
        todas = self.search([], order='secuencia')

        if not todas:
            return self._crear_config_default()

        if partner or move:
            business_unit_id = partner.business_unit_id.id if partner and partner.business_unit_id else False
            document_type_id = move.l10n_latam_document_type_id.id if move and move.l10n_latam_document_type_id else False

            for cfg in todas:
                if cfg.es_default:
                    continue
                bu_match = not cfg.business_unit_ids or (
                    business_unit_id and business_unit_id in cfg.business_unit_ids.ids
                )
                dt_match = not cfg.document_type_ids or (
                    document_type_id and document_type_id in cfg.document_type_ids.ids
                )
                if bu_match and dt_match:
                    return cfg

        # Fallback a la configuración por defecto
        default = todas.filtered(lambda c: c.es_default)
        if default:
            return default[0]

        # Si no hay default, retorna la primera
        return todas[0]

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