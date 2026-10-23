# Foto Facturación Perú (`foto_facturacion`)

Odoo 19 Community · localización `l10n_pe` · API de Gemini.

Botón **Rellenar con IA** en Facturación → Proveedores → Facturas (borrador). Toma el último adjunto de la
factura (PDF/PNG/JPG/WEBP, máx. 15 MB), lo envía a Gemini y rellena proveedor (por RUC; lo crea si no existe),
tipo y número de documento, fechas, moneda y líneas con IGV 18 %. La factura queda **siempre en borrador**.
En el chatter se dejan avisos (proveedor nuevo, total que no coincide, fecha dudosa, falta IGV 18 %).

## Configuración

| Qué | Dónde |
|---|---|
| Clave de la API | Variable de entorno `GEMINI_API_KEY` (recomendado) o parámetro de sistema `foto_facturacion.gemini_api_key` |
| Modelo | Parámetro de sistema `foto_facturacion.gemini_model` (por defecto `gemini-3.8-flash`) |

Requisitos: la empresa debe tener país Perú y el plan contable peruano cargado (impuesto de compra IGV 18 %).
La única dependencia de Python es `requests` (ya incluida con Odoo).

## Privacidad
Cada factura se envía a Google. Usa un proyecto de **pago** de Google AI (en la capa gratuita Google puede usar
los datos para mejorar sus modelos). El módulo no registra en el log el contenido de las facturas ni la clave.

## Pruebas
```bash
python3 odoo-bin -c ../config/odoo.conf -d <base_de_pruebas> -u foto_facturacion --test-tags /foto_facturacion --stop-after-init
```

## Despliegue en producción
Todo lo necesario viaja dentro de este módulo, en `deploy/`: guía completa [`deploy/DEPLOY.md`](deploy/DEPLOY.md),
`odoo.conf.prod.example`, `odoo.service`, `nginx-odoo.conf`, configuración de OpenBao y plantillas
`odoo.conf.dev.example` y `.env.example`. Resumen:
1. Copiar `foto_facturacion` a la carpeta de addons y añadirla a `addons_path`.
2. Definir `GEMINI_API_KEY` en el entorno del servicio de Odoo (p. ej. `Environment=` en systemd). No subir la clave a git.
3. Instalar: `-i foto_facturacion` (o Aplicaciones → Actualizar lista → Instalar).
4. `odoo.conf`: `admin_passwd` robusto, `list_db = False`, `dbfilter` fijo, `proxy_mode = True` si hay proxy inverso, `workers` ≥ 2.
5. Tiempos: la llamada a Gemini puede tardar hasta ~5 min en el peor caso (3 intentos de 90 s). Usa `limit_time_real = 360`
   en `odoo.conf` y `proxy_read_timeout 360s` en nginx. Ver `deploy/` (configuración, systemd, nginx, OpenBao).
6. Servir solo por HTTPS y hacer copias de seguridad de la base de datos.
7. Rotar la clave de Gemini si estuvo en algún archivo compartido.
# facturaConFotoIA
