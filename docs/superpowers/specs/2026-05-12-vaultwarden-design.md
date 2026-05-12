# Vaultwarden deployment — design

**Date:** 2026-05-12
**Target host:** `vaultwarden.ovh.zozoh.fr` (192.168.0.161)
**Public URL:** `https://vaultwarden.zozoh.fr`

## Goal

Deploy a Vaultwarden (Bitwarden-compatible) password manager on the
`vaultwarden.ovh.zozoh.fr` host, exposed publicly at `https://vaultwarden.zozoh.fr`
via the existing common proxy using SNI passthrough. The deployment must mirror
the established pattern of the `nextcloud` and `gitlab` roles: SSL terminates on
the backend host, the common proxy does `ssl_preread` and forwards encrypted
TLS over `proxy_protocol`.

## Configuration choices

| Setting          | Value                                                     |
|------------------|-----------------------------------------------------------|
| Database         | SQLite (file-backed at `/opt/vaultwarden/data`)            |
| Signups          | Disabled (`SIGNUPS_ALLOWED=false`)                         |
| Invitations      | Enabled (`INVITATIONS_ALLOWED=true`)                       |
| Admin page       | Enabled, gated by an argon2id-hashed `ADMIN_TOKEN`         |
| SMTP             | Relay through `smtp.zozoh.fr` as `vaultwarden@zozoh.fr`    |
| WebSocket        | On — served on the main HTTP port (Vaultwarden ≥ 1.29)     |
| Container image  | `vaultwarden/server:latest`                                |
| Data directory   | `/opt/vaultwarden/data` (UID/GID `1000:1000`)              |
| Host private IP  | `192.168.0.161` (network `192.168.0.160/27`)               |
| Admin network    | `192.168.1.160/27`                                         |

## Architecture

```
Internet → proxy1.ovh.zozoh.fr (192.168.0.225)
              │
              ├─ :80  nginx vhost  vaultwarden.zozoh.fr  → http://192.168.0.161
              │
              └─ :443 stream ssl_preread (SNI map)
                   vaultwarden.zozoh.fr → 192.168.0.161:443 (proxy_protocol)

                                          ↓
                vaultwarden.ovh.zozoh.fr (192.168.0.161)
                       │
                       ├─ nginx :80  → ACME webroot + 301 → https
                       └─ nginx :443 ssl http2 proxy_protocol
                              │
                              └─ proxy_pass http://127.0.0.1:8080
                                            │
                                            └─ docker: vaultwarden/server
                                                   /data → /opt/vaultwarden/data
```

SSL termination happens on the vaultwarden host. The cert lives in
`/etc/letsencrypt/live/vaultwarden.zozoh.fr/` once issued; a self-signed cert
under `/etc/nginx/ssl/vaultwarden.zozoh.fr/` is generated as a fallback so the
role is idempotent before the first Let's Encrypt run.

## Files to create

- `playbooks/vaultwarden.yaml` — applies `common` + `vaultwarden` roles
- `inventories/group_vars/vaultwarden/vars.yaml`
- `inventories/group_vars/vaultwarden/vault.yaml` (ansible-vault encrypted)
- `roles/vaultwarden/defaults/main.yml`
- `roles/vaultwarden/handlers/main.yml`
- `roles/vaultwarden/tasks/main.yml`
- `roles/vaultwarden/tasks/tasks.yml`
- `roles/vaultwarden/templates/docker-compose.yml.j2`
- `roles/vaultwarden/templates/vaultwarden-nginx.conf.j2`

## Files to modify

- `inventories/group_vars/common.yaml`:
  - Add `vaultwarden.zozoh.fr 192.168.0.161:443;` inside
    `map $ssl_preread_server_name $targetBackend`
  - Add a port-80 `nginx_vhosts` entry forwarding
    `http://vaultwarden.zozoh.fr/*` to `http://192.168.0.161` (so ACME
    challenges reach the backend and the backend can redirect to HTTPS)

No change needed to `inventories/all.yaml` — the `vaultwarden` group with
`vaultwarden.ovh.zozoh.fr` is already defined.

No change needed to the proxy's `certbot_certs` — the LE cert is issued on the
backend, not the proxy, matching the nextcloud pattern.

## Variables

### `inventories/group_vars/vaultwarden/vars.yaml`

```yaml
---
private_network: "192.168.0.160/27"
admin_network: "192.168.1.160/27"

group_hosts:
  vaultwarden.ovh.zozoh.fr: 192.168.0.161

# Domain
vaultwarden_domain: vaultwarden.zozoh.fr

# Docker image
vaultwarden_image: "vaultwarden/server:latest"

# Port bound to localhost, proxied by host nginx
vaultwarden_http_port: 8080

# Reverse proxy IP (for proxy_protocol / set_real_ip_from)
reverse_proxy_ip: 192.168.0.225

# Data directory on host
vaultwarden_data_dir: /opt/vaultwarden

# SMTP relay
vaultwarden_smtp_host: smtp.zozoh.fr
vaultwarden_smtp_port: 587
vaultwarden_smtp_security: starttls
vaultwarden_smtp_username: vaultwarden@zozoh.fr
vaultwarden_smtp_from: vaultwarden@zozoh.fr
```

### `inventories/group_vars/vaultwarden/vault.yaml` (encrypted)

```yaml
---
vaultwarden_admin_token: "$argon2id$v=19$m=65540,t=3,p=4$...."   # argon2id hash
vaultwarden_smtp_password: "<smtp-password>"
```

The admin token is generated **once** out-of-band:

```
docker run --rm -it vaultwarden/server /vaultwarden hash
```

and the resulting `$argon2id$…` hash is pasted into the vault. Storing the
hash (not plaintext) lets the encrypted vault be committed safely.

## Vaultwarden role

### Defaults

```yaml
# roles/vaultwarden/defaults/main.yml
---
role_vaultwarden_played: false
```

### Handlers

```yaml
# roles/vaultwarden/handlers/main.yml
---
- name: Reload nginx
  become: yes
  ansible.builtin.service:
    name: nginx
    state: reloaded

- name: Restart nginx
  become: yes
  ansible.builtin.service:
    name: nginx
    state: restarted

- name: Restart docker compose
  become: yes
  community.docker.docker_compose_v2:
    project_src: "{{ vaultwarden_data_dir }}"
    state: present
```

### Task order (`roles/vaultwarden/tasks/tasks.yml`)

1. Install `nginx`, `certbot`, `python3-certbot-nginx`, `openssl`
2. Create `/var/www/certbot` (mode `0755`)
3. Create `/etc/nginx/ssl/vaultwarden.zozoh.fr/` (mode `0700`)
4. Generate self-signed cert at that path if missing (`creates:` guard)
5. Stat `/etc/letsencrypt/live/vaultwarden.zozoh.fr/fullchain.pem`,
   set fact `vaultwarden_ssl_cert_path` to either the LE dir or the self-signed dir
6. Remove `/etc/nginx/sites-enabled/default`
7. Template `vaultwarden-nginx.conf.j2` →
   `/etc/nginx/sites-available/vaultwarden.conf` (notify `Restart nginx`)
8. Symlink it into `sites-enabled` (notify `Restart nginx`)
9. Weekly certbot renewal cron:
   `certbot renew --webroot -w /var/www/certbot --deploy-hook 'systemctl reload nginx' --quiet`
10. Create `/opt/vaultwarden/data` (owner `1000:1000`, mode `0750`)
11. Template `docker-compose.yml.j2` →
    `/opt/vaultwarden/docker-compose.yml` (notify `Restart docker compose`)
12. Deploy `/opt/vaultwarden/.env` (mode `0640`) with `ADMIN_TOKEN` and
    `SMTP_PASSWORD` from vault (notify `Restart docker compose`,
    `no_log: true`)
13. Ensure `nginx` is started + enabled
14. `community.docker.docker_compose_v2: project_src: /opt/vaultwarden` (up)
15. Set fact `role_vaultwarden_played: true`

Wrapper `tasks/main.yml` includes `tasks.yml` only when
`not role_vaultwarden_played` (same idempotency guard used by nextcloud).

### docker-compose.yml template

```yaml
services:
  vaultwarden:
    image: {{ vaultwarden_image }}
    restart: unless-stopped
    ports:
      - "127.0.0.1:{{ vaultwarden_http_port }}:80"
    volumes:
      - {{ vaultwarden_data_dir }}/data:/data
    environment:
      DOMAIN: "https://{{ vaultwarden_domain }}"
      SIGNUPS_ALLOWED: "false"
      INVITATIONS_ALLOWED: "true"
      SHOW_PASSWORD_HINT: "false"
      ADMIN_TOKEN: ${ADMIN_TOKEN}
      WEBSOCKET_ENABLED: "true"
      SMTP_HOST: "{{ vaultwarden_smtp_host }}"
      SMTP_FROM: "{{ vaultwarden_smtp_from }}"
      SMTP_FROM_NAME: "Vaultwarden"
      SMTP_PORT: "{{ vaultwarden_smtp_port }}"
      SMTP_SECURITY: "{{ vaultwarden_smtp_security }}"
      SMTP_USERNAME: "{{ vaultwarden_smtp_username }}"
      SMTP_PASSWORD: ${SMTP_PASSWORD}
      LOG_LEVEL: "warn"
      EXTENDED_LOGGING: "true"
```

### nginx vhost template

```nginx
# Vaultwarden — nginx reverse proxy with SSL
# Managed by Ansible — do not edit manually

map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 80;
    server_name {{ vaultwarden_domain }};

    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl http2 proxy_protocol;
    server_name {{ vaultwarden_domain }};

    ssl_certificate     {{ vaultwarden_ssl_cert_path }}/fullchain.pem;
    ssl_certificate_key {{ vaultwarden_ssl_cert_path }}/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    set_real_ip_from {{ reverse_proxy_ip }};
    real_ip_header   proxy_protocol;

    client_max_body_size 128M;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options    "nosniff"     always;
    add_header X-Frame-Options           "SAMEORIGIN"  always;
    add_header Referrer-Policy           "same-origin" always;

    location / {
        proxy_pass http://127.0.0.1:{{ vaultwarden_http_port }};
        proxy_http_version 1.1;
        proxy_set_header Upgrade           $http_upgrade;
        proxy_set_header Connection        $connection_upgrade;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $proxy_protocol_addr;
        proxy_set_header X-Forwarded-For   $proxy_protocol_addr;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

## Playbook

```yaml
# playbooks/vaultwarden.yaml
---
- hosts: vaultwarden
  become: yes
  vars:
    install_docker: true
    docker_registry_login: false
    sshd_listen: "{{ ansible_all_ipv4_addresses | ansible.netcommon.ipaddr(admin_network) | first }}"

  roles:
    - common
    - vaultwarden
```

## Common proxy changes

In `inventories/group_vars/common.yaml`:

1. **Stream SNI map** — append inside the existing
   `map $ssl_preread_server_name $targetBackend` block:

   ```
   vaultwarden.zozoh.fr 192.168.0.161:443;
   ```

2. **HTTP vhost** — append to `nginx_vhosts`:

   ```yaml
   - listen: "80"
     server_name: "vaultwarden.zozoh.fr"
     filename: "vaultwarden.zozoh.fr-80.conf"
     extra_parameters: |
       location / {
         gzip off;
         {{ _nginx_proxy_headers | indent(8) }}
         proxy_pass http://192.168.0.161;
       }
   ```

## Pre-deployment checklist (operator)

These are out-of-band steps the operator must do before / around running the
playbook:

1. **DNS:** point `vaultwarden.zozoh.fr` (A or CNAME) at the proxy's public IP.
2. **SMTP account:** create the mailbox `vaultwarden@zozoh.fr` on the mail
   server and note its password.
3. **Argon2 admin token:**
   `docker run --rm -it vaultwarden/server /vaultwarden hash`
   Save the hash into `inventories/group_vars/vaultwarden/vault.yaml` as
   `vaultwarden_admin_token`.
4. **Vault password:** the existing `vault-pass.sh` is already wired via
   `ansible.cfg`.

## Verification plan

After running `ansible-playbook playbooks/vaultwarden.yaml`:

1. From inside the network: `curl -k --resolve vaultwarden.zozoh.fr:443:192.168.0.161 https://vaultwarden.zozoh.fr/alive` → `OK`
2. From the Internet: `https://vaultwarden.zozoh.fr/` loads the Bitwarden web vault
3. `https://vaultwarden.zozoh.fr/admin` loads after entering the admin token
4. From `/admin`, invite a test user; confirm the invitation email is received
5. WebSocket sync: log in via the browser extension and confirm live updates
   propagate (validates `/notifications/hub` upgrade path)
6. Re-run the playbook to confirm idempotency (only the
   `community.docker.docker_compose_v2` task may report `changed` if container
   pull occurred)

## Out of scope

- Backups (a separate role / cron should snapshot `/opt/vaultwarden/data`)
- Fail2ban for `/admin` brute-force (Vaultwarden has built-in rate limits)
- Monitoring / alerting
- Migration from any existing Bitwarden instance (this is a fresh install)
