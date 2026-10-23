import base64
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestRellenarConIA(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.country_id = cls.env.ref('base.pe')
        cls.factura = cls.env['account.move'].create({'move_type': 'in_invoice'})
        cls.datos = {
            'ruc': '20506421781',
            'razon_social': 'CORPORACION RICO S.A.C.',
            'tipo_documento': '03',
            'serie': 'B613',
            'numero': '953765',
            'fecha_emision': '2026-05-27',
            'es_contado': True,
            'moneda': 'PEN',
            'lineas': [{'descripcion': 'POLLO ENTERO', 'cantidad': 2.91, 'precio_unitario': 7.97,
                        'afecto_igv': True}],
            'total': 27.35,
        }

    def _adjuntar(self, factura, nombre='f.jpg', mimetype='image/jpeg', datos=b'x'):
        return self.env['ir.attachment'].create({
            'name': nombre, 'mimetype': mimetype, 'raw': datos,
            'res_model': 'account.move', 'res_id': factura.id,
        })

    def test_sin_adjunto(self):
        with self.assertRaises(UserError):
            self.factura.action_rellenar_con_ia()

    def test_solo_borrador_de_proveedor(self):
        cliente = self.env['account.move'].create({'move_type': 'out_invoice'})
        with self.assertRaises(UserError):
            cliente.action_rellenar_con_ia()

    def test_archivo_demasiado_grande(self):
        adj = self._adjuntar(self.factura)
        with patch('odoo.addons.foto_facturacion.models.account_move.MAX_BYTES', 0):
            with self.assertRaises(UserError):
                self.factura._adjunto_factura()
        self.assertEqual(self.factura._adjunto_factura(), adj)

    def test_aplica_datos_y_crea_proveedor(self):
        existia = bool(self.env['res.partner'].search_count([('vat', '=', '20506421781')]))
        avisos = self.factura._aplicar_datos_ia(dict(self.datos))
        self.assertEqual(self.factura.partner_id.vat, '20506421781')
        self.assertEqual(str(self.factura.invoice_date), '2026-05-27')
        self.assertEqual(self.factura.invoice_date_due, self.factura.invoice_date)  # contado
        self.assertEqual(len(self.factura.invoice_line_ids), 1)
        # solo avisa de proveedor nuevo si realmente no existía en la base
        self.assertEqual(any('proveedor nuevo' in a for a in avisos), not existia)

    def test_aviso_si_total_no_coincide(self):
        datos = dict(self.datos, total=999.0)
        avisos = self.factura._aplicar_datos_ia(datos)
        self.assertTrue(any('no coincide' in a for a in avisos))

    def test_flujo_completo_con_gemini_simulado(self):
        self._adjuntar(self.factura, datos=base64.b64decode('/9j/4AAQSkZJRg=='))
        with patch.object(type(self.factura), '_gemini_extraer', return_value=dict(self.datos)):
            self.factura.action_rellenar_con_ia()
        self.assertEqual(self.factura.partner_id.vat, '20506421781')
        self.assertIn('Datos rellenados con IA', self.factura.message_ids[0].body)
