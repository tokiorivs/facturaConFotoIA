{
    'name': 'Foto Facturación Perú',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Localizations',
    'summary': 'Rellena facturas de proveedor peruanas desde una foto o PDF usando Gemini',
    'description': """
Añade el botón "Rellenar con IA" a las facturas de proveedor en borrador. Lee el último adjunto
(PDF, PNG, JPG o WEBP) con la API de Gemini y completa proveedor (RUC), tipo y número de documento,
fechas, moneda y líneas con IGV 18 %. Avisa en el chatter si el total no coincide o falta algún dato.
Ver README.md para la configuración.
""",
    'author': 'Cesar',
    'license': 'LGPL-3',
    'depends': ['account', 'l10n_pe'],
    'external_dependencies': {'python': ['requests']},
    'data': [
        'views/account_move_views.xml',
    ],
    'installable': True,
    'application': False,
}
