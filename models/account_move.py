import base64
import json
import logging
import os
import re
import time

import requests

from markupsafe import Markup, escape

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent'
DEFAULT_MODEL = 'gemini-3.8-flash'
MIMES_SOPORTADOS = ('application/pdf', 'image/png', 'image/jpeg', 'image/webp')
MAX_BYTES = 15 * 1024 * 1024  # la petición inline a Gemini admite ~20 MB en total (base64 incluido)
REINTENTOS = 2

PROMPT = (
    'Eres un asistente contable de Perú. Extrae los datos de esta factura o comprobante de pago '
    'del PROVEEDOR (el emisor, no el cliente). Devuelve únicamente los valores que aparezcan en el '
    'documento; si un dato no existe, déjalo vacío o en 0. Montos como número decimal sin símbolos ni '
    'separador de miles. Los precios unitarios de las líneas deben ir SIN IGV. Fecha en formato YYYY-MM-DD. '
    'tipo_documento: 01 factura, 03 boleta, 07 nota de crédito, 08 nota de débito. '
    'fecha_emision_texto: copia la fecha de emisión tal como está impresa, dígito por dígito (cuida el año). '
    'fecha_vencimiento: la fecha de vencimiento (F. Venc.) si aparece, vacía si no. '
    'es_contado: true si la forma de pago es contado/efectivo. total: el importe total final a pagar impreso en el documento '
    '(con IGV y redondeo), no el valor de venta.'
)

# Diferencia máxima (en la moneda del documento) que se tolera entre el total leído y el de Odoo,
# por el "importe de redondeo" que traen algunas facturas.
TOLERANCIA_TOTAL = 0.10

ESQUEMA = {
    'type': 'OBJECT',
    'properties': {
        'ruc': {'type': 'STRING', 'description': 'RUC del emisor, 11 dígitos'},
        'razon_social': {'type': 'STRING'},
        'tipo_documento': {'type': 'STRING'},
        'serie': {'type': 'STRING', 'description': 'Ej. F001'},
        'numero': {'type': 'STRING', 'description': 'Correlativo, solo dígitos'},
        'fecha_emision_texto': {'type': 'STRING', 'description': 'Fecha de emisión tal como se imprime'},
        'fecha_emision': {'type': 'STRING', 'description': 'YYYY-MM-DD'},
        'fecha_vencimiento': {'type': 'STRING', 'description': 'YYYY-MM-DD, vacío si no aparece'},
        'es_contado': {'type': 'BOOLEAN'},
        'moneda': {'type': 'STRING', 'description': 'PEN o USD'},
        'lineas': {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {
                    'descripcion': {'type': 'STRING'},
                    'cantidad': {'type': 'NUMBER'},
                    'precio_unitario': {'type': 'NUMBER'},
                    'afecto_igv': {'type': 'BOOLEAN'},
                },
                'required': ['descripcion', 'cantidad', 'precio_unitario'],
            },
        },
        'subtotal': {'type': 'NUMBER'},
        'igv': {'type': 'NUMBER'},
        'total': {'type': 'NUMBER'},
    },
    'propertyOrdering': [
        'ruc', 'razon_social', 'tipo_documento', 'serie', 'numero', 'fecha_emision_texto', 'fecha_emision',
        'fecha_vencimiento', 'es_contado', 'moneda', 'lineas', 'subtotal', 'igv', 'total',
    ],
    'required': ['ruc', 'serie', 'numero', 'total'],
}


class AccountMove(models.Model):
    _inherit = 'account.move'

    # ---- Gemini ----
    def _gemini_api_key(self):
        """Clave de la variable de entorno GEMINI_API_KEY o, si no existe, del parámetro de sistema
        foto_facturacion.gemini_api_key (solo visible para administradores)."""
        key = os.environ.get('GEMINI_API_KEY') or \
            self.env['ir.config_parameter'].sudo().get_param('foto_facturacion.gemini_api_key')
        if not key:
            raise UserError(_(
                'No se configuró la clave de Gemini. Define la variable de entorno GEMINI_API_KEY '
                'o el parámetro de sistema foto_facturacion.gemini_api_key.'))
        return key.strip()

    def _adjunto_factura(self):
        self.ensure_one()
        # Los adjuntos subidos desde el chatter cuelgan del mensaje, no siempre del registro.
        adjuntos = self.env['ir.attachment'].search([
            ('mimetype', 'in', MIMES_SOPORTADOS),
            '|',
            '&', ('res_model', '=', 'account.move'), ('res_id', '=', self.id),
            ('id', 'in', self.message_ids.attachment_ids.ids),
        ], order='id desc', limit=1)
        if not adjuntos:
            raise UserError(_('Adjunta primero la foto o el PDF de la factura (clip del chatter). '
                              'Formatos: PDF, PNG, JPG o WEBP.'))
        if adjuntos.file_size > MAX_BYTES:
            raise UserError(_('El archivo supera los %s MB permitidos. Reduce su tamaño o resolución.',
                              MAX_BYTES // (1024 * 1024)))
        return adjuntos

    def _gemini_post(self, modelo, cuerpo):
        """POST a Gemini con reintentos ante límites de uso (429) y errores temporales del servidor."""
        clave = self._gemini_api_key()
        for intento in range(REINTENTOS + 1):
            try:
                resp = requests.post(GEMINI_URL % modelo, json=cuerpo, timeout=(10, 90),
                                     headers={'x-goog-api-key': clave})
            except requests.RequestException as e:
                if intento == REINTENTOS:
                    _logger.warning('Gemini inaccesible (%s)', e.__class__.__name__)
                    raise UserError(_('No se pudo contactar con Gemini: %s', e.__class__.__name__))
            else:
                if resp.status_code not in (429, 500, 502, 503, 504) or intento == REINTENTOS:
                    return resp
            time.sleep(2 * (intento + 1))

    def _gemini_extraer(self, adjunto):
        modelo = self.env['ir.config_parameter'].sudo().get_param('foto_facturacion.gemini_model', DEFAULT_MODEL)
        cuerpo = {
            'contents': [{'parts': [
                {'inline_data': {'mime_type': adjunto.mimetype, 'data': base64.b64encode(adjunto.raw).decode()}},
                {'text': '%s La fecha de hoy es %s: ten presente que el año de los documentos recientes '
                         'es cercano a este; lee con atención cada dígito del año.'
                         % (PROMPT, fields.Date.context_today(self))},
            ]}],
            'generationConfig': {
                'mediaResolution': 'MEDIA_RESOLUTION_HIGH',
                'responseMimeType': 'application/json',
                'responseSchema': ESQUEMA,
                'temperature': 0,
            },
        }
        resp = self._gemini_post(modelo, cuerpo)
        if resp.status_code != 200:
            try:
                detalle = resp.json()['error']['message']
            except Exception:
                detalle = resp.reason
            # No se registra el cuerpo de la respuesta ni de la petición: contienen datos de la factura.
            _logger.warning('Gemini respondió %s (modelo %s)', resp.status_code, modelo)
            raise UserError(_('Gemini respondió con error %(code)s: %(msg)s', code=resp.status_code, msg=detalle))
        try:
            texto = resp.json()['candidates'][0]['content']['parts'][0]['text']
            datos = json.loads(texto)
        except (KeyError, IndexError, ValueError):
            _logger.warning('Respuesta de Gemini sin datos legibles (modelo %s)', modelo)
            raise UserError(_('Gemini no devolvió datos legibles. Prueba con una imagen más nítida.'))
        if not isinstance(datos, dict):
            raise UserError(_('Gemini no devolvió datos legibles. Prueba con una imagen más nítida.'))
        return datos

    # ---- Aplicar a la factura ----
    def action_rellenar_con_ia(self):
        self.ensure_one()
        if self.move_type != 'in_invoice' or self.state != 'draft':
            raise UserError(_('Solo se puede usar en facturas de proveedor en borrador.'))
        datos = self._gemini_extraer(self._adjunto_factura())
        avisos = self._aplicar_datos_ia(datos)
        cuerpo = _('Datos rellenados con IA (Gemini). Revisa los valores antes de confirmar.')
        if avisos:
            cuerpo += '<ul>%s</ul>' % ''.join('<li>%s</li>' % escape(a) for a in avisos)
        self.message_post(body=Markup(cuerpo))
        return True

    def _proveedor_por_ruc(self, ruc, razon_social):
        partner = self.env['res.partner'].search([('vat', '=', ruc)], limit=1)
        if partner or not ruc:
            return partner
        tipo_ruc = self.env.ref('l10n_pe.it_RUC', raise_if_not_found=False)
        return self.env['res.partner'].create({
            'name': razon_social or ruc,
            'vat': ruc,
            'l10n_latam_identification_type_id': tipo_ruc.id if tipo_ruc else False,
            'country_id': self.env.ref('base.pe').id,
            'company_type': 'company',
        })

    def _aplicar_datos_ia(self, d):
        self.ensure_one()
        avisos = []
        ruc = re.sub(r'\D', '', d.get('ruc') or '')
        vals = {}
        existia = bool(ruc) and bool(self.env['res.partner'].search_count([('vat', '=', ruc)]))
        partner = self._proveedor_por_ruc(ruc, d.get('razon_social'))
        if partner:
            vals['partner_id'] = partner.id
            if not existia:
                avisos.append(_('Se creó el proveedor nuevo %(name)s (RUC %(ruc)s). Verifica el RUC.',
                                name=partner.name, ruc=ruc))
        else:
            avisos.append(_('No se pudo leer el RUC del proveedor: elígelo manualmente.'))
        if d.get('fecha_emision'):
            vals['invoice_date'] = d['fecha_emision']
        vencimiento = d.get('fecha_vencimiento') or (d.get('es_contado') and d.get('fecha_emision'))
        if vencimiento:
            vals['invoice_date_due'] = vencimiento
        if d.get('fecha_emision'):
            try:
                emision = fields.Date.to_date(d['fecha_emision'])
                dias = (fields.Date.context_today(self) - emision).days
                if dias < 0 or dias > 366:
                    avisos.append(_('La fecha de emisión leída (%(fecha)s) es muy antigua o futura; el documento dice '
                                    '"%(texto)s". Verifica el año.',
                                    fecha=emision, texto=d.get('fecha_emision_texto') or '?'))
            except (ValueError, TypeError):
                avisos.append(_('La fecha de emisión leída no es válida: %s', d['fecha_emision']))
                vals.pop('invoice_date', None)
        moneda = self.env['res.currency'].search([('name', '=', (d.get('moneda') or '').upper())], limit=1)
        if moneda:
            vals['currency_id'] = moneda.id
        if vals:
            self.write(vals)

        tipo = self.env['l10n_latam.document.type'].search(
            [('code', '=', d.get('tipo_documento') or '01'), ('country_id.code', '=', 'PE')], limit=1)
        if tipo:
            self.l10n_latam_document_type_id = tipo
        if d.get('serie') and d.get('numero'):
            numero = re.sub(r'\D', '', d['numero']).zfill(8)
            self.l10n_latam_document_number = '%s-%s' % (d['serie'].upper(), numero)

        igv = self.env['account.tax'].search([
            ('type_tax_use', '=', 'purchase'), ('amount_type', '=', 'percent'), ('amount', '=', 18),
            ('company_id', '=', self.company_id.id)], limit=1)
        if not igv:
            avisos.append(_('No existe un impuesto de compra IGV 18 % en esta base: las líneas quedaron sin IGV. '
                            'Configura la localización peruana.'))
        lineas = []
        for ln in d.get('lineas') or []:
            lineas.append((0, 0, {
                'name': ln.get('descripcion') or _('Línea'),
                'quantity': ln.get('cantidad') or 1,
                'price_unit': ln.get('precio_unitario') or 0,
                'tax_ids': [(6, 0, igv.ids if ln.get('afecto_igv', True) and igv else [])],
            }))
        if not lineas and d.get('total'):
            base = d.get('subtotal') or (d['total'] - (d.get('igv') or 0))
            lineas.append((0, 0, {
                'name': _('Factura %s-%s', d.get('serie') or '', d.get('numero') or ''),
                'quantity': 1,
                'price_unit': base,
                'tax_ids': [(6, 0, igv.ids if igv and d.get('igv') else [])],
            }))
        if lineas:
            self.write({'invoice_line_ids': [(5, 0, 0)] + lineas})

        total_doc = d.get('total') or 0
        if total_doc:
            diferencia = round(self.amount_total - total_doc, 2)
            if abs(diferencia) > TOLERANCIA_TOTAL:
                avisos.append(_('El total del documento (%(doc).2f) no coincide con el de Odoo (%(odoo).2f). '
                                'Revisa líneas e impuestos.', doc=total_doc, odoo=self.amount_total))
        else:
            avisos.append(_('No se pudo leer el total del documento: no se pudo verificar el importe.'))
        return avisos
