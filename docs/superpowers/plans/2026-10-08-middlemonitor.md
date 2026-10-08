# Middle Monitor server and agent fleet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a self-hosted Middle Monitor server on the new `monitor` VM and install its host agent on every other managed machine so each reports to it.

**Architecture:** Two Ansible roles. `middlemonitor` fetches the upstream self-hosted Docker Compose stack unchanged, templates only the `.env` (Caddy bound to localhost), and fronts it with nginx + certbot for TLS, exactly like the `vaultwarden` role; proxy1 reaches it by SNI passthrough. `middlemonitor-agent` is self-contained: it downloads the agent binary from the server's own API with a checksum from the API's `/sha256` endpoint, templates a validated `config.yaml`, and runs it under systemd.

**Tech Stack:** Ansible, Docker Compose (`community.docker.docker_compose_v2`), nginx, certbot, systemd, Debian 12.

**Spec:** `docs/superpowers/specs/2026-10-07-middlemonitor-design.md`

## Global Constraints

- Prose and identifiers in English; no em dashes or en dashes anywhere (user rule 13). Use the repo's `# -- section --` comment style, not box-drawing characters.
- Role shape matches the repo convention: `tasks/main.yml` includes `tasks.yml` guarded by `when: not role_<name>_played`, and `tasks.yml` ends by setting `role_<name>_played: true`. Defaults set the flag to `false`.
- This project has no role unit-test framework (no molecule). Roles are verified by `ansible-playbook --syntax-check`, idempotence (a second run reports `changed=0` on the role's tasks), and functional checks on the target. Do not add a test framework.
- Secrets live only in `*/vault.yaml`, encrypted with `./encrypt-vault-values.py <file>` (writes plaintext first, then run the script, which encrypts values in place with `ansible-vault encrypt_string`). `vault_password_file` is already set in `ansible.cfg`. Tasks that render secrets use `no_log: true`.
- Server host: `monitor.ovh.zozoh.fr`, vmid 211. Service IP `192.168.3.1` (block `192.168.3.0/27`, gw `192.168.3.30`). Admin IP `192.168.4.1` (block `192.168.4.0/27`, gw `192.168.4.30`). Public domain `monitor.zozoh.fr`.
- Pinning: `middlemonitor_ref: main` (git ref for the fetched compose/Caddyfile) and `middlemonitor_version: latest` (ghcr image tag `MM_VERSION`). Middle Monitor is young; change these deliberately.
- reverse_proxy_ip (proxy1) is `192.168.0.225`.
- The VM already exists (created by the operator); this plan only configures it.

## Review Focus

- **Agent architecture mapping (odroid is arm64).** `ansible_architecture` is `aarch64` on the odroid and `x86_64` on the OVH VMs; the download URL needs `arm64`/`amd64`. A wrong or missing map silently fetches the wrong binary or 404s. Pinned by the arch-map test in Task 4.
- **Missing install token.** Running the agent play before the token exists must fail with a clear message, not deploy an agent that cannot register. Pinned by the assert step in Task 4.
- **Caddy bind address.** If `HTTP_PORT` is a bare port, Caddy binds `0.0.0.0:8000` and the stack is reachable unauthenticated on the VM's IPs. It must be `127.0.0.1:8000`. Pinned by the localhost-bind check in Task 2.
- **Certificate issuance ordering.** certbot HTTP-01 needs proxy1's `:80` vhost live first, or the public site serves the self-signed fallback (browser warning). Pinned by the cert verification step in Task 3.
- **Agent re-run churn.** A second agent run must not re-download the binary or restart the service (ETag/304 + unchanged config). Pinned by the idempotence step in Task 5.

---

### Task 1: Monitor inventory, group_vars and server vault

**Files:**
- Modify: `inventories/all.yaml` (add the `monitor` group/host)
- Create: `inventories/group_vars/monitor/vars.yaml`
- Create: `inventories/group_vars/monitor/vault.yaml`

**Interfaces:**
- Produces: inventory host `monitor.ovh.zozoh.fr` in group `monitor`; vars `private_network`, `admin_network`, `group_hosts`, `middlemonitor_domain`, `reverse_proxy_ip`, `middlemonitor_ref`, `middlemonitor_version`; vault vars `middlemonitor_jwt_secret`, `middlemonitor_db_password`, `middlemonitor_credentials_encryption_key`, `middlemonitor_unsub_secret`, `middlemonitor_seed_admin_email`, `middlemonitor_seed_admin_password` (and optional SMTP).

- [ ] **Step 1: Add the monitor group to `inventories/all.yaml`**

Append under `all.children`:

```yaml
    monitor:
      hosts:
        monitor.ovh.zozoh.fr:
          proxmox_vmid: 211
```

- [ ] **Step 2: Create `inventories/group_vars/monitor/vars.yaml`**

```yaml
---
private_network: "192.168.3.0/27"
admin_network: "192.168.4.0/27"

group_hosts:
  monitor.ovh.zozoh.fr: 192.168.3.1

# Public domain, fronted by proxy1 (TLS terminated on this VM)
middlemonitor_domain: monitor.zozoh.fr

# proxy1, the only host trusted to send the PROXY protocol header on :443
reverse_proxy_ip: 192.168.0.225

# Pinning. Middle Monitor is young; bump these deliberately.
middlemonitor_ref: main        # git ref for the fetched compose/Caddyfile
middlemonitor_version: latest  # MM_VERSION, the ghcr image tag
```

- [ ] **Step 3: Create `inventories/group_vars/monitor/vault.yaml` with plaintext values**

Generate the secrets, then write the file:

```bash
echo "jwt:   $(openssl rand -base64 32)"
echo "dbpw:  $(openssl rand -base64 32)"
echo "crypt: $(openssl rand -base64 32)"
echo "unsub: $(openssl rand -hex 32)"
```

```yaml
---
middlemonitor_jwt_secret: "<jwt>"
middlemonitor_db_password: "<dbpw>"
middlemonitor_credentials_encryption_key: "<crypt>"
middlemonitor_unsub_secret: "<unsub>"
middlemonitor_seed_admin_email: "enzo@zozoh.fr"
middlemonitor_seed_admin_password: "<a strong password you choose>"
```

- [ ] **Step 4: Encrypt the vault file in place**

Run: `./encrypt-vault-values.py inventories/group_vars/monitor/vault.yaml`
Expected: `inventories/group_vars/monitor/vault.yaml: 6 encrypted, 0 already encrypted`. Confirm each value is now a `!vault |` block.

- [ ] **Step 5: Verify the inventory resolves**

Run: `ansible-inventory -i inventories/ --host monitor.ovh.zozoh.fr`
Expected: JSON shows `private_network`, `admin_network`, `middlemonitor_domain: monitor.zozoh.fr`, and the vault vars (decrypted, since `vault_password_file` is set). No parse error.

- [ ] **Step 6: Commit**

```bash
git add inventories/all.yaml inventories/group_vars/monitor/
git commit -m "feat(middlemonitor): monitor inventory, group_vars and server vault"
```

---

### Task 2: `middlemonitor` role and playbook (deploy the server)

**Files:**
- Create: `roles/middlemonitor/defaults/main.yml`
- Create: `roles/middlemonitor/tasks/main.yml`
- Create: `roles/middlemonitor/tasks/tasks.yml`
- Create: `roles/middlemonitor/handlers/main.yml`
- Create: `roles/middlemonitor/templates/env.j2`
- Create: `roles/middlemonitor/templates/middlemonitor-nginx.conf.j2`
- Create: `playbooks/middlemonitor.yaml`

**Interfaces:**
- Consumes: vars from Task 1; `install_docker`, `docker_registry_login`, `sshd_listen` set by the playbook; the `common` role (Docker install).
- Produces: a running stack in `{{ middlemonitor_dir }}` (default `/opt/middlemonitor`) answering on `127.0.0.1:8000`, fronted by nginx on `:443` (proxy_protocol) and `:80` (ACME + redirect); fact `role_middlemonitor_played`.

- [ ] **Step 1: Create `roles/middlemonitor/defaults/main.yml`**

```yaml
---
role_middlemonitor_played: false

middlemonitor_dir: /opt/middlemonitor

# HTTP_PORT passed to the upstream compose line "${HTTP_PORT:-8000}:80".
# Binding to 127.0.0.1 keeps the stack reachable only through the host nginx.
middlemonitor_bind: "127.0.0.1:8000"
# What the host nginx reverse-proxies to.
middlemonitor_upstream: "127.0.0.1:8000"

middlemonitor_public_url: "https://{{ middlemonitor_domain }}"

# Upstream deploy files are fetched unchanged from this base at middlemonitor_ref.
middlemonitor_raw_base: "https://raw.githubusercontent.com/middle-monitor/middle-monitor/{{ middlemonitor_ref }}/deploy/self-hosted"

# Root-cause explanation engine: "rules" needs nothing external.
middlemonitor_explain_mode: rules

# Optional SMTP for invitations and alert mail. Empty leaves it off.
middlemonitor_smtp_host: ""
middlemonitor_smtp_port: 587
middlemonitor_smtp_user: ""
middlemonitor_smtp_from: ""
```

- [ ] **Step 2: Create `roles/middlemonitor/tasks/main.yml`**

```yaml
---
- name: Include middlemonitor task
  ansible.builtin.include_tasks: tasks.yml
  when: not role_middlemonitor_played
```

- [ ] **Step 3: Create `roles/middlemonitor/handlers/main.yml`**

```yaml
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
    project_src: "{{ middlemonitor_dir }}"
    state: present
```

- [ ] **Step 4: Create `roles/middlemonitor/templates/env.j2`**

```jinja
# Managed by Ansible - do not edit manually
MM_VERSION={{ middlemonitor_version }}
PUBLIC_URL={{ middlemonitor_public_url }}
HTTP_PORT={{ middlemonitor_bind }}

JWT_SECRET={{ middlemonitor_jwt_secret }}
DB_PASSWORD={{ middlemonitor_db_password }}
CREDENTIALS_ENCRYPTION_KEY={{ middlemonitor_credentials_encryption_key }}
UNSUB_SECRET={{ middlemonitor_unsub_secret }}

SEED_ADMIN_EMAIL={{ middlemonitor_seed_admin_email }}
SEED_ADMIN_PASSWORD={{ middlemonitor_seed_admin_password }}

EXPLAIN_MODE={{ middlemonitor_explain_mode }}
{% if middlemonitor_smtp_host %}
SMTP_HOST={{ middlemonitor_smtp_host }}
SMTP_PORT={{ middlemonitor_smtp_port }}
SMTP_USER={{ middlemonitor_smtp_user }}
SMTP_PASS={{ middlemonitor_smtp_password }}
SMTP_FROM={{ middlemonitor_smtp_from }}
{% endif %}
```

- [ ] **Step 5: Create `roles/middlemonitor/templates/middlemonitor-nginx.conf.j2`**

Mirror of `roles/vaultwarden/templates/vaultwarden-nginx.conf.j2`, proxying to the Caddy upstream:

```jinja
# Middle Monitor - nginx reverse proxy with SSL
# Managed by Ansible - do not edit manually

map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 80;
    server_name {{ middlemonitor_domain }};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2 proxy_protocol;
    server_name {{ middlemonitor_domain }};

    ssl_certificate     {{ middlemonitor_ssl_cert_path }}/fullchain.pem;
    ssl_certificate_key {{ middlemonitor_ssl_cert_path }}/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    set_real_ip_from  {{ reverse_proxy_ip }};
    real_ip_header    proxy_protocol;

    client_max_body_size 128M;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options    "nosniff"      always;
    add_header X-Frame-Options           "SAMEORIGIN"   always;
    add_header Referrer-Policy           "same-origin"  always;

    location / {
        proxy_pass http://{{ middlemonitor_upstream }};
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

- [ ] **Step 6: Create `roles/middlemonitor/tasks/tasks.yml`**

```yaml
---
# -- Upstream deploy files, fetched unchanged --
- name: Create middlemonitor directory
  ansible.builtin.file:
    path: "{{ middlemonitor_dir }}"
    state: directory
    mode: "0750"

- name: Fetch upstream docker-compose.yml
  ansible.builtin.get_url:
    url: "{{ middlemonitor_raw_base }}/docker-compose.yml"
    dest: "{{ middlemonitor_dir }}/docker-compose.yml"
    mode: "0644"
  notify: Restart docker compose

- name: Fetch upstream Caddyfile
  ansible.builtin.get_url:
    url: "{{ middlemonitor_raw_base }}/Caddyfile"
    dest: "{{ middlemonitor_dir }}/Caddyfile"
    mode: "0644"
  notify: Restart docker compose

- name: Deploy .env file
  ansible.builtin.template:
    src: env.j2
    dest: "{{ middlemonitor_dir }}/.env"
    mode: "0640"
  notify: Restart docker compose
  no_log: true

# -- nginx and certbot front (self-terminated TLS, proxy1 passthrough) --
- name: Install nginx and certbot
  ansible.builtin.apt:
    name:
      - nginx
      - certbot
      - python3-certbot-nginx
      - openssl
    state: present

- name: Create certbot webroot directory
  ansible.builtin.file:
    path: /var/www/certbot
    state: directory
    mode: "0755"

- name: Create SSL directory for self-signed certificate
  ansible.builtin.file:
    path: "/etc/nginx/ssl/{{ middlemonitor_domain }}"
    state: directory
    mode: "0700"

- name: Generate self-signed certificate for {{ middlemonitor_domain }}
  ansible.builtin.command: >
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048
    -keyout /etc/nginx/ssl/{{ middlemonitor_domain }}/privkey.pem
    -out /etc/nginx/ssl/{{ middlemonitor_domain }}/fullchain.pem
    -subj "/CN={{ middlemonitor_domain }}"
  args:
    creates: "/etc/nginx/ssl/{{ middlemonitor_domain }}/fullchain.pem"

- name: Check if Let's Encrypt certificate exists
  ansible.builtin.stat:
    path: "/etc/letsencrypt/live/{{ middlemonitor_domain }}/fullchain.pem"
  register: letsencrypt_cert

- name: Set SSL certificate path
  ansible.builtin.set_fact:
    middlemonitor_ssl_cert_path: "{{ '/etc/letsencrypt/live/' + middlemonitor_domain if letsencrypt_cert.stat.exists else '/etc/nginx/ssl/' + middlemonitor_domain }}"

- name: Remove default nginx site
  ansible.builtin.file:
    path: /etc/nginx/sites-enabled/default
    state: absent

- name: Deploy nginx configuration
  ansible.builtin.template:
    src: middlemonitor-nginx.conf.j2
    dest: /etc/nginx/sites-available/middlemonitor.conf
    mode: "0644"
  notify: Restart nginx

- name: Enable nginx site
  ansible.builtin.file:
    src: /etc/nginx/sites-available/middlemonitor.conf
    dest: /etc/nginx/sites-enabled/middlemonitor.conf
    state: link
  notify: Restart nginx

- name: Setup certbot renewal cron
  ansible.builtin.cron:
    name: "certbot renew"
    minute: "30"
    hour: "2"
    weekday: "1"
    job: "certbot renew --webroot -w /var/www/certbot --deploy-hook 'systemctl reload nginx' --quiet"
    user: root

# -- Start services --
- name: Ensure nginx is started and enabled
  ansible.builtin.service:
    name: nginx
    state: started
    enabled: true

- name: Start docker-compose
  community.docker.docker_compose_v2:
    project_src: "{{ middlemonitor_dir }}"
  register: middlemonitor_compose_output

- name: Role middlemonitor played
  ansible.builtin.set_fact:
    role_middlemonitor_played: true
```

- [ ] **Step 7: Create `playbooks/middlemonitor.yaml`**

```yaml
---
- hosts: monitor
  become: yes
  vars:
    install_docker: true
    docker_registry_login: false
    sshd_listen: "{{ ansible_all_ipv4_addresses | ansible.netcommon.ipaddr(admin_network) | first }}"

  roles:
    - common
    - middlemonitor
```

- [ ] **Step 8: Syntax check**

Run: `ansible-playbook -i inventories/ playbooks/middlemonitor.yaml --syntax-check`
Expected: no errors.

- [ ] **Step 9: Deploy to the VM**

Run: `ansible-playbook -i inventories/ playbooks/middlemonitor.yaml --limit monitor.ovh.zozoh.fr`
Expected: completes without failed tasks. First run pulls the ghcr images (several minutes).

- [ ] **Step 10: Verify the stack answers and Caddy is bound to localhost (Review Focus: Caddy bind address)**

On the VM:
- Run: `curl -fsS http://127.0.0.1:8000/api/v1/openapi.json >/dev/null && echo OK`
  Expected: `OK` (the api answers through Caddy).
- Run: `ss -ltnp | grep ':8000'`
  Expected: the listener is on `127.0.0.1:8000`, never `0.0.0.0:8000`.
- Run: `docker compose -f /opt/middlemonitor/docker-compose.yml ps`
  Expected: `api`, `receiver`, `worker`, `frontend`, `caddy`, `postgres`, `opensearch`, `kafka` all up.

- [ ] **Step 11: Verify idempotence**

Run the Step 9 command again.
Expected: `changed=0` for the role's tasks (no handler fires: `.env`, compose and Caddyfile unchanged).

- [ ] **Step 12: Commit**

```bash
git add roles/middlemonitor/ playbooks/middlemonitor.yaml
git commit -m "feat(middlemonitor): server role and playbook (compose stack + nginx TLS front)"
```

---

### Task 3: Expose the server through proxy1 and issue the certificate

**Files:**
- Modify: `inventories/group_vars/common.yaml` (additive: `group_hosts`, `nginx_upstreams`, stream map, `:80` vhost)

**Interfaces:**
- Consumes: the running server from Task 2; proxy1's existing `geerlingguy.nginx` config.
- Produces: `https://monitor.zozoh.fr` fronted by proxy1 (SNI passthrough) and an LE certificate on the VM.

- [ ] **Step 1: Add proxy1 entries to `inventories/group_vars/common.yaml`**

Under `group_hosts`, add:

```yaml
  monitor: 192.168.3.1
```

Under `nginx_upstreams`, add:

```yaml
  - name: monitor
    servers:
      - "monitor:80"
```

Inside the `stream { map $ssl_preread_server_name $targetBackend { ... } }` block (alongside the other service lines, before the `*.zozoh.fr` wildcard), add:

```nginx
      monitor.zozoh.fr 192.168.3.1:443;
```

Under `nginx_vhosts`, add a port-80 vhost (ACME HTTP-01 plus redirect are handled on the VM):

```yaml
  - listen: "80"
    server_name: "monitor.zozoh.fr"
    filename: "monitor.zozoh.fr-80.conf"
    extra_parameters: |
      location / {
        gzip                    off;

        {{ _nginx_proxy_headers | indent(8) }}

        proxy_pass http://monitor;
      }
```

- [ ] **Step 2: Syntax check and deploy proxy1**

Run: `ansible-playbook -i inventories/ playbooks/proxy.yaml --syntax-check`
Then: `ansible-playbook -i inventories/ playbooks/proxy.yaml`
Expected: completes; nginx reloads on proxy1.

- [ ] **Step 3: Confirm the DNS record exists**

`monitor.zozoh.fr` must resolve to proxy1's public IP (same as the other `*.zozoh.fr` services). If it does not, add the record before issuing the cert. Verify: `dig +short monitor.zozoh.fr`.

- [ ] **Step 4: Issue the LE certificate on the VM (Review Focus: certificate issuance ordering)**

This runs only after Step 2 (proxy1 fronts `:80`). On the monitor VM:

Run: `sudo certbot certonly --webroot -w /var/www/certbot -d monitor.zozoh.fr --non-interactive --agree-tos -m enzo@zozoh.fr`
Then: `sudo systemctl reload nginx`
Expected: certbot reports success; nginx now serves the LE cert (the role's `Set SSL certificate path` picks `/etc/letsencrypt/...` on the next playbook run).

- [ ] **Step 5: Verify public access with a valid certificate**

Run: `curl -fsS https://monitor.zozoh.fr/api/v1/openapi.json >/dev/null && echo OK`
Expected: `OK` with no TLS error (valid LE cert, proxy_protocol path working end to end).
Also open `https://monitor.zozoh.fr` in a browser and sign in with the seeded admin.

- [ ] **Step 6: Confirm existing services are unaffected**

Spot-check `https://vaultwarden.zozoh.fr` and `https://gitlab.zozoh.fr` still load (SNI precedence: the explicit `monitor.zozoh.fr` entry does not disturb other names).

- [ ] **Step 7: Commit**

```bash
git add inventories/group_vars/common.yaml
git commit -m "feat(middlemonitor): front monitor.zozoh.fr through proxy1 SNI passthrough"
```

---

### Task 4: `middlemonitor-agent` role, agent vault, odroid inventory and playbook

**Files:**
- Create: `roles/middlemonitor-agent/defaults/main.yml`
- Create: `roles/middlemonitor-agent/vars/main.yml`
- Create: `roles/middlemonitor-agent/tasks/main.yml`
- Create: `roles/middlemonitor-agent/tasks/tasks.yml`
- Create: `roles/middlemonitor-agent/tasks/arch_map_assert.yml`
- Create: `roles/middlemonitor-agent/handlers/main.yml`
- Create: `roles/middlemonitor-agent/templates/config.yaml.j2`
- Create: `roles/middlemonitor-agent/templates/middle-monitor-agent.service.j2`
- Modify: `inventories/all.yaml` (add the `odroid` group)
- Modify: `inventories/group_vars/all/vars.yaml` (add `middlemonitor_agent_api_host_ip`)
- Modify: `inventories/group_vars/all/vault.yaml` (add `middlemonitor_agent_api_key`, placeholder until Task 5)
- Create: `playbooks/middlemonitor-agent.yaml`

**Interfaces:**
- Consumes: `middlemonitor_agent_api_key` (vault), `middlemonitor_agent_api_host_ip` (group var), `inventory_hostname`.
- Produces: `/usr/local/bin/middle-monitor-agent` (symlink), `/etc/middle-monitor/config.yaml`, a running `middle-monitor-agent.service`; fact `role_middlemonitor_agent_played`.

- [ ] **Step 1: Create `roles/middlemonitor-agent/defaults/main.yml`**

```yaml
---
role_middlemonitor_agent_played: false

middlemonitor_agent_api_url: "https://monitor.zozoh.fr"
middlemonitor_agent_config_dir: /etc/middle-monitor
middlemonitor_agent_bin_dir: /usr/local/bin

# Point the API hostname at a private IP on hosts that have one (skips the proxy
# hairpin). Empty means use public DNS (the odroid). Set in group_vars.
middlemonitor_agent_api_host_ip: ""

middlemonitor_agent_cpu: true
middlemonitor_agent_ram: true
middlemonitor_agent_disk: true
middlemonitor_agent_network: true
middlemonitor_agent_interval: 60
```

- [ ] **Step 2: Create `roles/middlemonitor-agent/vars/main.yml`**

```yaml
---
# Map Ansible's uname-style arch to the agent's download naming.
_middlemonitor_agent_arch_map:
  x86_64: amd64
  aarch64: arm64
  armv7l: arm
middlemonitor_agent_os: "{{ ansible_system | lower }}"
middlemonitor_agent_arch: "{{ _middlemonitor_agent_arch_map[ansible_architecture] | default(ansible_architecture) }}"
middlemonitor_agent_api_host: "{{ middlemonitor_agent_api_url | urlsplit('hostname') }}"
```

- [ ] **Step 3: Write the arch-map test (Review Focus: agent architecture mapping)**

Create `roles/middlemonitor-agent/tasks/arch_map_assert.yml` as a self-check included at the top of `tasks.yml` (Step 5):

```yaml
---
- name: Assert the architecture maps to a known agent platform
  ansible.builtin.assert:
    that:
      - middlemonitor_agent_arch in ['amd64', 'arm64', 'arm']
    fail_msg: >
      Unmapped architecture '{{ ansible_architecture }}' -> '{{ middlemonitor_agent_arch }}'.
      Add it to _middlemonitor_agent_arch_map in roles/middlemonitor-agent/vars/main.yml.
```

- [ ] **Step 4: Create `roles/middlemonitor-agent/tasks/main.yml`**

```yaml
---
- name: Include middlemonitor-agent task
  ansible.builtin.include_tasks: tasks.yml
  when: not role_middlemonitor_agent_played
```

- [ ] **Step 5: Create `roles/middlemonitor-agent/tasks/tasks.yml`**

```yaml
---
- name: Validate the resolved architecture
  ansible.builtin.import_tasks: arch_map_assert.yml

- name: Fail early when the install token is missing
  ansible.builtin.assert:
    that:
      - middlemonitor_agent_api_key | default('') | length > 0
    fail_msg: >
      middlemonitor_agent_api_key is not set. Generate an install token in the
      Middle Monitor UI ({{ middlemonitor_agent_api_url }}) and store it encrypted
      in inventories/group_vars/all/vault.yaml.

- name: Point the API hostname at its private IP (skip the proxy hairpin)
  ansible.builtin.lineinfile:
    path: /etc/hosts
    regexp: "\\s{{ middlemonitor_agent_api_host | regex_escape }}$"
    line: "{{ middlemonitor_agent_api_host_ip }}\t{{ middlemonitor_agent_api_host }}"
    state: present
  when: middlemonitor_agent_api_host_ip | length > 0

- name: Create config directory
  ansible.builtin.file:
    path: "{{ middlemonitor_agent_config_dir }}"
    state: directory
    mode: "0750"

# Fetch the digest ourselves and pass it as a bare hash. get_url's URL-checksum
# path matches by the download URL's basename ("amd64"), which a shasum-format
# file never contains; the first token of the response is the hash either way.
- name: Fetch the published agent checksum
  ansible.builtin.uri:
    url: "{{ middlemonitor_agent_api_url }}/api/v1/agents/download/{{ middlemonitor_agent_os }}/{{ middlemonitor_agent_arch }}/sha256"
    return_content: true
  register: middlemonitor_agent_sha

- name: Download the agent binary (verified against the published checksum)
  ansible.builtin.get_url:
    url: "{{ middlemonitor_agent_api_url }}/api/v1/agents/download/{{ middlemonitor_agent_os }}/{{ middlemonitor_agent_arch }}"
    dest: "{{ middlemonitor_agent_bin_dir }}/middle-monitor-agent-{{ middlemonitor_agent_os }}-{{ middlemonitor_agent_arch }}"
    checksum: "sha256:{{ middlemonitor_agent_sha.content.split() | first }}"
    mode: "0755"
  notify: Restart middle-monitor-agent

- name: Symlink the agent binary to a stable name
  ansible.builtin.file:
    src: "{{ middlemonitor_agent_bin_dir }}/middle-monitor-agent-{{ middlemonitor_agent_os }}-{{ middlemonitor_agent_arch }}"
    dest: "{{ middlemonitor_agent_bin_dir }}/middle-monitor-agent"
    state: link
  notify: Restart middle-monitor-agent

- name: Deploy agent configuration
  ansible.builtin.template:
    src: config.yaml.j2
    dest: "{{ middlemonitor_agent_config_dir }}/config.yaml"
    mode: "0640"
    validate: "{{ middlemonitor_agent_bin_dir }}/middle-monitor-agent --config-check --config %s"
  notify: Reload middle-monitor-agent
  no_log: true

- name: Deploy systemd unit
  ansible.builtin.template:
    src: middle-monitor-agent.service.j2
    dest: /etc/systemd/system/middle-monitor-agent.service
    mode: "0644"
  notify:
    - Reload systemd
    - Restart middle-monitor-agent

- name: Enable and start the agent
  ansible.builtin.systemd:
    name: middle-monitor-agent
    enabled: true
    state: started
    daemon_reload: true

- name: Role middlemonitor-agent played
  ansible.builtin.set_fact:
    role_middlemonitor_agent_played: true
```

- [ ] **Step 6: Create `roles/middlemonitor-agent/handlers/main.yml`**

```yaml
---
- name: Reload systemd
  become: yes
  ansible.builtin.systemd:
    daemon_reload: true

- name: Reload middle-monitor-agent
  become: yes
  ansible.builtin.service:
    name: middle-monitor-agent
    state: reloaded

- name: Restart middle-monitor-agent
  become: yes
  ansible.builtin.service:
    name: middle-monitor-agent
    state: restarted
```

- [ ] **Step 7: Create `roles/middlemonitor-agent/templates/config.yaml.j2`**

```jinja
# Managed by Ansible - do not edit manually
api:
  url: "{{ middlemonitor_agent_api_url }}"
  api_key: "{{ middlemonitor_agent_api_key }}"

host:
  name: "{{ inventory_hostname }}"

metrics:
  cpu: {{ middlemonitor_agent_cpu | lower }}
  ram: {{ middlemonitor_agent_ram | lower }}
  disk: {{ middlemonitor_agent_disk | lower }}
  network: {{ middlemonitor_agent_network | lower }}

interval: {{ middlemonitor_agent_interval }}
```

- [ ] **Step 8: Create `roles/middlemonitor-agent/templates/middle-monitor-agent.service.j2`**

```jinja
# Managed by Ansible - do not edit manually
[Unit]
Description=Middle Monitor agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={{ middlemonitor_agent_bin_dir }}/middle-monitor-agent --config {{ middlemonitor_agent_config_dir }}/config.yaml
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 9: Add the odroid group to `inventories/all.yaml`**

Append under `all.children`. The odroid reaches the API over the public URL, so its `middlemonitor_agent_api_host_ip` is empty:

```yaml
    odroid:
      hosts:
        odroid.home:
          ansible_host: 83.205.35.25
          ansible_port: 2222
          ansible_user: enzo
          middlemonitor_agent_api_host_ip: ""
```

(The host is named `odroid.home`, distinct from the `odroid` group, to avoid an Ansible group/host name collision that otherwise drops the host var override.)

- [ ] **Step 10: Add the private-IP var to `inventories/group_vars/all/vars.yaml`**

OVH VMs resolve the API to its private IP (the odroid overrides this to empty in Step 9):

```yaml
# Agents on the OVH network reach the Middle Monitor API by its private IP,
# skipping the proxy hairpin. Overridden to "" on hosts that must use public DNS.
middlemonitor_agent_api_host_ip: 192.168.3.1
```

- [ ] **Step 11: Add a placeholder token to `inventories/group_vars/all/vault.yaml`**

Add the key with an empty string for now (real value lands in Task 5 once the server can mint it). Add the plaintext line, then re-run `./encrypt-vault-values.py inventories/group_vars/all/vault.yaml`:

```yaml
middlemonitor_agent_api_key: ""
```

Note: the Task 5 assert will intentionally block deployment until this holds a real token. (Leaving it empty keeps the file valid and the OVH service playbooks unaffected.)

- [ ] **Step 12: Create `playbooks/middlemonitor-agent.yaml`**

```yaml
---
- hosts: all:!monitor
  become: yes
  roles:
    - middlemonitor-agent
```

- [ ] **Step 13: Syntax check and arch resolution**

Run: `ansible-playbook -i inventories/ playbooks/middlemonitor-agent.yaml --syntax-check`
Expected: no errors.
Run: `ansible -i inventories/ all:!monitor -m debug -a "msg={{ ansible_architecture }}"` (gathers facts) is optional; the arch assert runs during the real deploy in Task 5.

- [ ] **Step 14: Commit**

```bash
git add roles/middlemonitor-agent/ playbooks/middlemonitor-agent.yaml inventories/all.yaml inventories/group_vars/all/
git commit -m "feat(middlemonitor-agent): agent role, odroid inventory and fleet playbook"
```

---

### Task 5: Mint the token, canary one host, then roll out the fleet

**Files:**
- Modify: `inventories/group_vars/all/vault.yaml` (real token)

**Interfaces:**
- Consumes: the running server (Tasks 2 and 3), the agent role (Task 4).
- Produces: every OVH VM and the odroid reporting into the dashboard.

- [ ] **Step 1: Generate the install token**

In the Middle Monitor UI (`https://monitor.zozoh.fr`, signed in as the seeded admin), create the organization install token / API key for agents. Copy it.

- [ ] **Step 2: Store the token in the vault**

Replace the placeholder in `inventories/group_vars/all/vault.yaml` with the real token (set it back to plaintext for the one line, then re-encrypt):

Run: `./encrypt-vault-values.py inventories/group_vars/all/vault.yaml`
Verify: `ansible-inventory -i inventories/ --host gitlab.ovh.zozoh.fr | grep -c middlemonitor_agent_api_key` returns `1` and the value decrypts.

- [ ] **Step 3: Canary deploy to one OVH host**

Run: `ansible-playbook -i inventories/ playbooks/middlemonitor-agent.yaml --limit vaultwarden.ovh.zozoh.fr`
Expected: completes; the arch assert passes (`amd64`), the binary downloads, `--config-check` passes, the service starts.

- [ ] **Step 4: Verify the canary**

On `vaultwarden.ovh.zozoh.fr`:
- Run: `systemctl status middle-monitor-agent` -> active (running).
- Run: `journalctl -u middle-monitor-agent -n 20 --no-pager` -> registered, no auth error.
- In the dashboard, confirm `vaultwarden.ovh.zozoh.fr` appears as a host reporting CPU/memory/disk/network.

- [ ] **Step 5: Verify agent idempotence (Review Focus: agent re-run churn)**

Run Step 3 again.
Expected: `changed=0`. The binary is not re-downloaded (checksum/ETag match), the config is unchanged, and the service is not restarted.

- [ ] **Step 6: Roll out to the remaining OVH hosts**

Run: `ansible-playbook -i inventories/ playbooks/middlemonitor-agent.yaml --limit 'all:!monitor:!odroid:!vaultwarden.ovh.zozoh.fr'`
Expected: all succeed. Confirm each host appears in the dashboard.

- [ ] **Step 7: Deploy to the odroid**

Run: `ansible-playbook -i inventories/ playbooks/middlemonitor-agent.yaml --limit odroid`
Expected: the `arm64` binary installs; the agent uses the public URL (no `/etc/hosts` override). Confirm `odroid` appears in the dashboard.
Note: if `become` on the odroid needs a sudo password, add `--ask-become-pass` or set `ansible_become_password` in the odroid vault.

- [ ] **Step 8: Commit**

```bash
git add inventories/group_vars/all/vault.yaml
git commit -m "feat(middlemonitor-agent): deploy agent fleet-wide (install token)"
```

---

## Notes and deviations from the spec

- **API hostname resolution.** The spec proposed a `global_hosts` entry applied by the `common` role. The plan instead keeps this inside the agent role as a var-driven `/etc/hosts` line (`middlemonitor_agent_api_host_ip`), so the agent role stays self-contained and runs identically on the odroid (empty = public DNS) without pulling in the `common` role. Set once in `group_vars/all`, overridden to empty on the odroid.
- **Scrape / Prometheus targets.** Omitted (YAGNI). The role ships host metrics only. Prometheus scraping, `scrape.d` fragments and discovery are documented upstream and can be layered on later via the same config template.
- **Initial LE certificate** is a one-time manual `certbot certonly` (Task 3, Step 4), matching how `vaultwarden` is handled; the role manages only renewal and cert-path selection.
