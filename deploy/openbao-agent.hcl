# /etc/openbao/agent.hcl
vault {
  address = "http://127.0.0.1:8200"
}

auto_auth {
  method "approle" {
    config = {
      role_id_file_path                   = "/etc/openbao/role_id"
      secret_id_file_path                 = "/etc/openbao/secret_id"
      remove_secret_id_file_after_reading = false
    }
  }
}

template {
  contents    = <<EOT
{{ with secret "secret/data/foto_facturacion" }}GEMINI_API_KEY={{ .Data.data.GEMINI_API_KEY }}{{ end }}
EOT
  destination = "/run/odoo-env/env"
  perms       = "0600"
  # Si la clave cambia en OpenBao, reinicia Odoo para que la lea (try-restart no lo arranca si está parado)
  command     = "systemctl try-restart odoo"
}
