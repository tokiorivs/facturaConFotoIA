# /etc/openbao/openbao.hcl  (servidor OpenBao de un solo nodo, solo accesible desde el propio VPS)
ui = false

storage "raft" {
  path    = "/var/lib/openbao"
  node_id = "vps1"
}

listener "tcp" {
  address     = "127.0.0.1:8200"
  tls_disable = true
}

api_addr     = "http://127.0.0.1:8200"
cluster_addr = "http://127.0.0.1:8201"
