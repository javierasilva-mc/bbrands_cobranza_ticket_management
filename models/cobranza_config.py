# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError


class CobranzaConfig(models.Model):
    _name = 'cobranza.config'
    _description = 'Configuración del módulo de cobranza'

    name = fields.Char(default='Configuración de Cobranza')

    # Configuración general
    dias_vencimiento = fields.Integer(
        string='Días de vencimiento',
        default=10,
        help='Días desde la fecha de emisión para crear un ticket de cobranza.',
    )
    ticket_type_id = fields.Many2one(
        'ticket.type',
        string='Tipo de ticket',
    )
    category_id = fields.Many2one(
        'ticket.category',
        string='Categoría',
    )
    subcategory_id = fields.Many2one(
        'ticket.subcategory',
        string='Subcategoría',
    )
    subcategory_template_id = fields.Many2one(
        'subcategory.template',
        string='Plantilla de subcategoría',
    )
    stage_cerrado_id = fields.Many2one(
        'helpdesk.ticket.stage',
        string='Stage cerrado',
    )
    stage_in_progress_id = fields.Many2one(
        'helpdesk.ticket.stage',
        string='Stage reapertura',
    )
    team_id = fields.Many2one(
        'helpdesk.team',
        string='Equipo',
    )
    ejecutivo_default_id = fields.Many2one(
        'res.users',
        string='Ejecutivo por defecto',
    )

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
    
    stage_tarea_completada_id = fields.Many2one(
        'project.task.stage',
        string='Stage tarea completada',
    )
    
    incluir_in_payment = fields.Boolean(
        string='Incluir "En proceso de pago" como pendiente',
        default=False,
        help='Si está activo, las boletas en estado "En proceso de pago" también '
            'se consideran pendientes para efectos de cobranza.',
    )

    @api.model
    def get_config(self):
        config = self.search([], limit=1)
        if not config:
            config = self._crear_config_default()
        return config

    def _crear_config_default(self):
        """Crea la configuración con los valores por defecto del ambiente actual."""
        stage_cerrado = self.env['helpdesk.ticket.stage'].search(
            [('name', '=', 'Closed')], limit=1)
        stage_in_progress = self.env['helpdesk.ticket.stage'].search(
            [('name', '=', 'In-progress')], limit=1)

        config = self.create({
            'dias_vencimiento': 10,
            'stage_cerrado_id': stage_cerrado.id if stage_cerrado else False,
            'stage_in_progress_id': stage_in_progress.id if stage_in_progress else False,
        })
        
        stage_tarea_completada = self.env['project.task.stage'].search(
            [('name', '=', 'Done')], limit=1)

        config = self.create({
            'dias_vencimiento': 10,
            'stage_cerrado_id': stage_cerrado.id if stage_cerrado else False,
            'stage_in_progress_id': stage_in_progress.id if stage_in_progress else False,
            'stage_tarea_completada_id': stage_tarea_completada.id if stage_tarea_completada else False,
        })

        # Tareas iniciales por defecto
        tmpl_contacto = self.env['task.template'].search(
            [('name', '=', 'Contacto inicial')], limit=1)
        tmpl_acuerdo = self.env['task.template'].search(
            [('name', '=', 'Acuerdo de pago')], limit=1)

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

        # Regla de seguimiento por defecto
        tmpl_seguimiento = self.env['task.template'].search(
            [('name', '=', 'Seguimiento de acuerdo')], limit=1)

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
        config = self.get_config()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Configuración de Cobranza',
            'res_model': 'cobranza.config',
            'view_mode': 'form',
            'res_id': config.id,
            'target': 'current',
        }


class CobranzaConfigTarea(models.Model):
    _name = 'cobranza.config.tarea'
    _description = 'Tarea inicial de cobranza'
    _order = 'secuencia'

    config_id = fields.Many2one(
        'cobranza.config',
        string='Configuración',
        required=True,
        ondelete='cascade',
    )
    secuencia = fields.Integer(string='Secuencia', default=10)
    task_template_id = fields.Many2one(
        'task.template',
        string='Plantilla de tarea',
        required=True,
    )
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

    config_id = fields.Many2one(
        'cobranza.config',
        string='Configuración',
        required=True,
        ondelete='cascade',
    )
    secuencia = fields.Integer(string='Secuencia', default=10)
    tarea_disparadora_id = fields.Many2one(
        'task.template',
        string='Tarea disparadora',
        required=True,
        help='Plantilla de tarea cuya fecha de acuerdo dispara esta regla.',
    )
    condicion = fields.Selection([
        ('fecha_vencida',     'Fecha de acuerdo vencida'),
        ('dias_ultima_tarea', 'Días desde tarea completada'),
    ], string='Condición', required=True, default='fecha_vencida')
    dias_condicion = fields.Integer(
        string='Días',
        default=3,
        help='Días desde que se completó la tarea disparadora para crear las tareas de seguimiento.',
    )
    requiere_tarea_completada = fields.Boolean(
        string='Requiere tarea completada',
        default=True,
        help='Si está activo, la tarea disparadora debe estar completada para que se ejecute la regla.',
    )
    tarea_ids = fields.One2many(
        'cobranza.config.regla.tarea',
        'regla_id',
        string='Tareas a crear',
    )
    
    @api.constrains('dias_condicion', 'condicion')
    def _check_dias_condicion(self):
        for rec in self:
            if rec.condicion == 'dias_ultima_tarea' and rec.dias_condicion < 1:
                raise UserError(
                    'Los días de la condición deben ser mayor a 0.'
                )
                
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

    regla_id = fields.Many2one(
        'cobranza.config.regla',
        string='Regla',
        required=True,
        ondelete='cascade',
    )
    secuencia = fields.Integer(string='Secuencia', default=10)
    task_template_id = fields.Many2one(
        'task.template',
        string='Plantilla de tarea',
        required=True,
    )