# Despliegue en un VPS Linux (Ubuntu 24.04)

Supuestos: usuario con `sudo`, dominio apuntando al VPS, Odoo 19 + `foto_facturacion`, OpenBao en el mismo VPS.
Rutas: `/opt/odoo/{odoo,custom_addons,venv}`, config en `/etc/odoo/odoo.conf`.

## 1. Paquetes del sistema
```bash
sudo apt update && sudo apt install -y git python3-full python3-venv python3-dev build-essential \
  libxml2-dev libxslt1-dev zlib1g-dev libsasl2-dev libldap2-dev libssl-dev libffi-dev libjpeg-dev \
  libpq-dev liblcms2-dev postgresql nginx certbot python3-certbot-nginx wkhtmltopdf ufw
```

## 2. Usuario, código y entorno virtual
```bash
sudo adduser --system --group --home /opt/odoo --shell /bin/bash odoo
sudo -u odoo git clone --depth 1 --branch 19.0 https://github.com/odoo/odoo.git /opt/odoo/odoo
sudo -u odoo mkdir -p /opt/odoo/custom_addons
```
Sube **solo este módulo** (la carpeta `foto_facturacion`, con su `deploy/`, `README.md` y `tests/`) a
`/opt/odoo/custom_addons/foto_facturacion` (la carpeta debe llamarse exactamente así). Por git:
```bash
sudo -u odoo git clone <URL_DE_TU_REPO> /opt/odoo/custom_addons/foto_facturacion
```
o por `scp`/`rsync`. Todas las rutas de esta guía parten de esa ubicación. Después:
```bash
sudo -u odoo python3 -m venv /opt/odoo/venv
sudo -u odoo /opt/odoo/venv/bin/pip install -r /opt/odoo/odoo/requirements.txt
sudo mkdir -p /etc/odoo /var/lib/odoo /var/log/odoo
sudo chown odoo:odoo /var/lib/odoo /var/log/odoo
```

## 3. PostgreSQL
```bash
sudo -u postgres createuser --pwprompt odoo        # el rol NO es superusuario
```
Pon esa contraseña en `db_password` de `/etc/odoo/odoo.conf` (copia de `deploy/odoo.conf.prod.example` (dentro del módulo)).
```bash
sudo cp /opt/odoo/custom_addons/foto_facturacion/deploy/odoo.conf.prod.example /etc/odoo/odoo.conf
sudo chown root:odoo /etc/odoo/odoo.conf && sudo chmod 640 /etc/odoo/odoo.conf
sudo nano /etc/odoo/odoo.conf     # admin_passwd, db_password, db_name
```
Para crear la base con la localización peruana (el país debe fijarse al crearla, así se carga el IGV 18 %):
```bash
sudo -u odoo /opt/odoo/venv/bin/python /opt/odoo/odoo/odoo-bin db init midb \
  -c /etc/odoo/odoo.conf --country=pe --language=es_PE --password='CLAVE_DEL_ADMIN'
sudo -u odoo /opt/odoo/venv/bin/python /opt/odoo/odoo/odoo-bin module install account foto_facturacion \
  -c /etc/odoo/odoo.conf -d midb
```
> Si `db init` rechaza un parámetro, ejecuta con `--help`: la sintaxis puede variar entre versiones.

## 4. OpenBao (guarda `GEMINI_API_KEY`)
Descarga el `.deb` de OpenBao desde https://github.com/openbao/openbao/releases e instálalo:
```bash
sudo apt install ./openbao_*_linux_amd64.deb
sudo cp /opt/odoo/custom_addons/foto_facturacion/deploy/openbao.hcl /etc/openbao/openbao.hcl
sudo mkdir -p /var/lib/openbao && sudo chown openbao:openbao /var/lib/openbao
sudo systemctl enable --now openbao
export BAO_ADDR=http://127.0.0.1:8200
bao operator init -key-shares=3 -key-threshold=2
```
**Guarda las 3 claves de desbloqueo (unseal) y el root token en un gestor de contraseñas, fuera del VPS.** Sin ellas no se recupera nada.
```bash
bao operator unseal        # repite con 2 claves distintas
export BAO_TOKEN=<root token>
bao secrets enable -path=secret kv-v2
bao kv put secret/foto_facturacion GEMINI_API_KEY=-      # pega la clave, Enter, Ctrl+D
bao policy write odoo-foto /opt/odoo/custom_addons/foto_facturacion/deploy/odoo-foto-policy.hcl
bao auth enable approle
bao write auth/approle/role/odoo token_policies=odoo-foto token_ttl=1h
bao read -field=role_id auth/approle/role/odoo/role-id | sudo tee /etc/openbao/role_id
bao write -f -field=secret_id auth/approle/role/odoo/secret-id | sudo tee /etc/openbao/secret_id
sudo chmod 600 /etc/openbao/role_id /etc/openbao/secret_id
unset BAO_TOKEN            # y revoca/guarda aparte el root token
```

## 5. Servicios
```bash
sudo cp /opt/odoo/custom_addons/foto_facturacion/deploy/openbao-agent.hcl /etc/openbao/agent.hcl
sudo cp /opt/odoo/custom_addons/foto_facturacion/deploy/openbao-agent.service /etc/systemd/system/
sudo cp /opt/odoo/custom_addons/foto_facturacion/deploy/odoo.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openbao-agent odoo
sudo systemctl status odoo
```
Comprueba que el agente escribió la clave sin mostrarla: `sudo test -s /run/odoo-env/env && echo OK`.

### Después de reiniciar el VPS
OpenBao arranca **sellado**: hay que ejecutar `bao operator unseal` (2 claves) antes de que el agente pueda entregar la clave. Mientras tanto Odoo no arranca (falla con "No llego GEMINI_API_KEY"). Si no quieres este paso manual, usa auto-unseal o una de las alternativas de abajo.

## 6. Nginx y HTTPS
```bash
sudo cp /opt/odoo/custom_addons/foto_facturacion/deploy/nginx-odoo.conf /etc/nginx/sites-available/odoo     # edita server_name
sudo ln -s /etc/nginx/sites-available/odoo /etc/nginx/sites-enabled/odoo
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d facturas.midominio.com
```

## 7. Cortafuegos
```bash
sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable
```
No abras 8069, 8072, 8200 ni 5432: solo se usan en local.

## 8. Copias de seguridad
Base de datos y filestore, diario, fuera del VPS:
```bash
sudo -u postgres pg_dump -Fc midb > /ruta/backup/midb_$(date +%F).dump
sudo tar czf /ruta/backup/filestore_$(date +%F).tgz -C /var/lib/odoo filestore
```
Respalda también `/var/lib/openbao` y, por separado, las claves de unseal.

## Alternativa más simple (sin OpenBao)
En un único VPS, si el paso de unseal manual es un problema: crea `/etc/odoo/gemini.env` con `GEMINI_API_KEY=...`,
`chmod 600`, propietario root, y en `odoo.service` usa `EnvironmentFile=/etc/odoo/gemini.env` (quita el
`ExecStartPre` y la dependencia del agente). Menos seguridad, pero sin sellado.
