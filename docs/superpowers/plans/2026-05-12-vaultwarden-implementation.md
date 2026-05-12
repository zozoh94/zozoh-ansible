# Vaultwarden Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy Vaultwarden on `vaultwarden.ovh.zozoh.fr` (192.168.0.161) exposed publicly at `https://vaultwarden.zozoh.fr` via the existing common proxy using SNI passthrough.

**Architecture:** Ansible role `vaultwarden` mirrors the `nextcloud` pattern. Local nginx on the backend host terminates TLS with `proxy_protocol`; common proxy does `ssl_preread` and forwards encrypted TLS. Vaultwarden runs in docker-compose bound to `127.0.0.1:8080`. SQLite, signups disabled, invitations enabled, SMTP via `smtp.zozoh.fr` (account `vaultwarden@zozoh.fr`), admin page gated by an argon2id-hashed `ADMIN_TOKEN`.

**Tech Stack:** Ansible 2.x, Jinja2 templates, `community.docker.docker_compose_v2`, `vaultwarden/server` Docker image, nginx (host), Let's Encrypt via webroot.

**Spec:** `docs/superpowers/specs/2026-05-12-vaultwarden-design.md`

**Working directory:** `/home/eahameli/perso/zozoh-ansible`

---

## Verification convention

Ansible has no traditional TDD cycle, but every task that produces a YAML/Jinja file ends with:
1. `ansible-playbook --syntax-check playbooks/vaultwarden.yaml` (for playbook/role)
2. Linter-style inspection (parse with `python -c "import yaml; yaml.safe_load(open(...))"` for YAML, or render the Jinja in `--check` mode where applicable).
3. Commit.

For task 11 onwards (apply on real hosts), commands must be run from the project root with the user's existing ansible environment.

---

## Task 0: Operator pre-flight (out-of-band)

These are manual steps the operator performs once. They are **not** part of any automated task, but must be complete before Task 11.

**Files:** none

- [ ] **Step 1: DNS** — point `vaultwarden.zozoh.fr` (A or CNAME) at the proxy's public IP (same record style as `cloud.zozoh.fr`).

- [ ] **Step 2: Mailbox** — create `vaultwarden@zozoh.fr` on the mail server; note its SMTP password.

- [ ] **Step 3: Argon2 admin token** — on any host with docker, run:

  ```bash
  docker run --rm -it vaultwarden/server /vaultwarden hash
  ```

  Enter the desired admin-page passphrase. Copy the output line starting with `$argon2id$v=19$...`. This hash (not the passphrase) goes into the vault in Task 2.

- [ ] **Step 4: Confirm** vault password script `./vault-pass.sh` is executable and works:

  ```bash
  ./vault-pass.sh && echo OK
  ```

  Expected: prints the vault password followed by `OK`.

---

## Task 1: Create `inventories/group_vars/vaultwarden/vars.yaml`

**Files:**
- Create: `inventories/group_vars/vaultwarden/vars.yaml`

- [ ] **Step 1: Create the directory**

  ```bash
  mkdir -p inventories/group_vars/vaultwarden
  ```

- [ ] **Step 2: Write `vars.yaml`**

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

- [ ] **Step 3: Validate YAML**

  ```bash
  python3 -c "import yaml; yaml.safe_load(open('inventories/group_vars/vaultwarden/vars.yaml'))" && echo OK
  ```

  Expected: `OK`.

- [ ] **Step 4: Commit**

  ```bash
  git add inventories/group_vars/vaultwarden/vars.yaml
  git commit -m "feat(vaultwarden): add group_vars vars.yaml"
  ```

---

## Task 2: Create `inventories/group_vars/vaultwarden/vault.yaml` (encrypted)

**Files:**
- Create: `inventories/group_vars/vaultwarden/vault.yaml`

- [ ] **Step 1: Write the plaintext vault skeleton to a temp file**

  ```bash
  cat > /tmp/vw-vault.yaml <<'EOF'
  ---
  vaultwarden_admin_token: "PASTE_ARGON2_HASH_HERE"
  vaultwarden_smtp_password: "PASTE_SMTP_PASSWORD_HERE"
  EOF
  ```

- [ ] **Step 2: Replace the placeholders** with the real values from Task 0 (argon2 hash and SMTP password). Edit `/tmp/vw-vault.yaml` directly.

  The argon2 hash must be **double-quoted** in YAML so the `$` chars are not interpreted by any shell that might cat the file.

- [ ] **Step 3: Encrypt the values inline**

  Use `ansible-vault encrypt_string` so that values are encrypted **inline** (matches existing pattern in `inventories/group_vars/nextcloud/vault.yaml`):

  ```bash
  TOKEN=$(grep '^vaultwarden_admin_token:' /tmp/vw-vault.yaml | sed -E 's/^vaultwarden_admin_token: *"(.*)"$/\1/')
  SMTPP=$(grep '^vaultwarden_smtp_password:' /tmp/vw-vault.yaml | sed -E 's/^vaultwarden_smtp_password: *"(.*)"$/\1/')

  {
    echo "---"
    ansible-vault encrypt_string --name 'vaultwarden_admin_token' "$TOKEN"
    echo
    ansible-vault encrypt_string --name 'vaultwarden_smtp_password' "$SMTPP"
    echo
  } > inventories/group_vars/vaultwarden/vault.yaml
  ```

- [ ] **Step 4: Wipe the temp file**

  ```bash
  shred -u /tmp/vw-vault.yaml 2>/dev/null || rm -f /tmp/vw-vault.yaml
  ```

- [ ] **Step 5: Verify decryption works**

  ```bash
  ansible-vault view inventories/group_vars/vaultwarden/vault.yaml | head
  ```

  Expected: the cleartext YAML appears, starting with `vaultwarden_admin_token: $argon2id$...`.

- [ ] **Step 6: Commit**

  ```bash
  git add inventories/group_vars/vaultwarden/vault.yaml
  git commit -m "feat(vaultwarden): add encrypted vault with admin token and SMTP password"
  ```

---

## Task 3: Create role skeleton — `defaults`, `handlers`, `tasks/main.yml`

**Files:**
- Create: `roles/vaultwarden/defaults/main.yml`
- Create: `roles/vaultwarden/handlers/main.yml`
- Create: `roles/vaultwarden/tasks/main.yml`

- [ ] **Step 1: Create the role directory tree**

  ```bash
  mkdir -p roles/vaultwarden/{defaults,handlers,tasks,templates}
  ```

- [ ] **Step 2: Write `roles/vaultwarden/defaults/main.yml`**

  ```yaml
  ---
  role_vaultwarden_played: false
  ```

- [ ] **Step 3: Write `roles/vaultwarden/handlers/main.yml`**

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
      project_src: "{{ vaultwarden_data_dir }}"
      state: present
  ```

- [ ] **Step 4: Write `roles/vaultwarden/tasks/main.yml`**

  ```yaml
  ---
  - name: Include vaultwarden task
    ansible.builtin.include_tasks: tasks.yml
    when: not role_vaultwarden_played
  ```

- [ ] **Step 5: YAML-validate all three**

  ```bash
  for f in roles/vaultwarden/defaults/main.yml roles/vaultwarden/handlers/main.yml roles/vaultwarden/tasks/main.yml; do
    python3 -c "import yaml; yaml.safe_load(open('$f'))" && echo "OK $f"
  done
  ```

  Expected: three `OK ...` lines.

- [ ] **Step 6: Commit**

  ```bash
  git add roles/vaultwarden/defaults roles/vaultwarden/handlers roles/vaultwarden/tasks/main.yml
  git commit -m "feat(vaultwarden): scaffold role with defaults, handlers, main tasks"
  ```

---

## Task 4: Create `roles/vaultwarden/tasks/tasks.yml`

**Files:**
- Create: `roles/vaultwarden/tasks/tasks.yml`

- [ ] **Step 1: Write the file**

  ```yaml
  ---
  # ── Nginx & Certbot ──────────────────────────────────────────────

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
      path: "/etc/nginx/ssl/{{ vaultwarden_domain }}"
      state: directory
      mode: "0700"

  - name: Generate self-signed certificate for {{ vaultwarden_domain }}
    ansible.builtin.command: >
      openssl req -x509 -nodes -days 3650 -newkey rsa:2048
      -keyout /etc/nginx/ssl/{{ vaultwarden_domain }}/privkey.pem
      -out /etc/nginx/ssl/{{ vaultwarden_domain }}/fullchain.pem
      -subj "/CN={{ vaultwarden_domain }}"
    args:
      creates: "/etc/nginx/ssl/{{ vaultwarden_domain }}/fullchain.pem"

  - name: Check if Let's Encrypt certificate exists
    ansible.builtin.stat:
      path: "/etc/letsencrypt/live/{{ vaultwarden_domain }}/fullchain.pem"
    register: letsencrypt_cert

  - name: Set SSL certificate path
    ansible.builtin.set_fact:
      vaultwarden_ssl_cert_path: "{{ '/etc/letsencrypt/live/' + vaultwarden_domain if letsencrypt_cert.stat.exists else '/etc/nginx/ssl/' + vaultwarden_domain }}"

  - name: Remove default nginx site
    ansible.builtin.file:
      path: /etc/nginx/sites-enabled/default
      state: absent

  - name: Deploy nginx configuration
    ansible.builtin.template:
      src: vaultwarden-nginx.conf.j2
      dest: /etc/nginx/sites-available/vaultwarden.conf
      mode: "0644"
    notify: Restart nginx

  - name: Enable nginx site
    ansible.builtin.file:
      src: /etc/nginx/sites-available/vaultwarden.conf
      dest: /etc/nginx/sites-enabled/vaultwarden.conf
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

  # ── Docker Compose ────────────────────────────────────────────────

  - name: Create vaultwarden data directory
    ansible.builtin.file:
      path: "{{ vaultwarden_data_dir }}/data"
      state: directory
      mode: "0750"
      owner: "1000"
      group: "1000"

  - name: Deploy docker-compose.yml
    ansible.builtin.template:
      src: docker-compose.yml.j2
      dest: "{{ vaultwarden_data_dir }}/docker-compose.yml"
      mode: "0644"
    notify: Restart docker compose

  - name: Deploy .env file
    ansible.builtin.copy:
      dest: "{{ vaultwarden_data_dir }}/.env"
      mode: "0640"
      content: |
        ADMIN_TOKEN='{{ vaultwarden_admin_token }}'
        SMTP_PASSWORD='{{ vaultwarden_smtp_password }}'
    notify: Restart docker compose
    no_log: true

  # ── Start services ────────────────────────────────────────────────

  - name: Ensure nginx is started and enabled
    ansible.builtin.service:
      name: nginx
      state: started
      enabled: true

  - name: Start docker-compose
    community.docker.docker_compose_v2:
      project_src: "{{ vaultwarden_data_dir }}"
    register: vaultwarden_compose_output

  - ansible.builtin.debug:
      var: vaultwarden_compose_output

  - name: Role vaultwarden played
    ansible.builtin.set_fact:
      role_vaultwarden_played: true
  ```

- [ ] **Step 2: YAML-validate**

  ```bash
  python3 -c "import yaml; yaml.safe_load(open('roles/vaultwarden/tasks/tasks.yml'))" && echo OK
  ```

  Expected: `OK`.

- [ ] **Step 3: Commit** (deferred until templates exist — see Task 6 commit step)

  *(The role isn't usable without the templates referenced inside, so we'll commit together. Move on to Task 5.)*

---

## Task 5: Create `roles/vaultwarden/templates/docker-compose.yml.j2`

**Files:**
- Create: `roles/vaultwarden/templates/docker-compose.yml.j2`

- [ ] **Step 1: Write the template**

  ```jinja
  ---

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

  **Why the mix of `{{ ... }}` and `${...}`:** Jinja `{{ }}` is rendered by Ansible at template time and produces the static values. `${ADMIN_TOKEN}` and `${SMTP_PASSWORD}` are docker-compose substitutions resolved at compose-up time from the `.env` file. This keeps the secret hash/password out of the rendered docker-compose.yml file on disk.

- [ ] **Step 2: Render-test by inspecting the rendered output for the nextcloud pattern**

  No standalone render test (Jinja is rendered during the play). The Ansible run in Task 11 is the verification.

---

## Task 6: Create `roles/vaultwarden/templates/vaultwarden-nginx.conf.j2`

**Files:**
- Create: `roles/vaultwarden/templates/vaultwarden-nginx.conf.j2`

- [ ] **Step 1: Write the template**

  ```jinja
  # Vaultwarden - nginx reverse proxy with SSL
  # Managed by Ansible — do not edit manually

  map $http_upgrade $connection_upgrade {
      default upgrade;
      ''      close;
  }

  # ── HTTP: certbot challenges + redirect ──────────────────────────

  server {
      listen 80;
      server_name {{ vaultwarden_domain }};

      location /.well-known/acme-challenge/ {
          root /var/www/certbot;
      }

      location / {
          return 301 https://$host$request_uri;
      }
  }

  # ── HTTPS: Vaultwarden ───────────────────────────────────────────

  server {
      listen 443 ssl http2 proxy_protocol;
      server_name {{ vaultwarden_domain }};

      ssl_certificate     {{ vaultwarden_ssl_cert_path }}/fullchain.pem;
      ssl_certificate_key {{ vaultwarden_ssl_cert_path }}/privkey.pem;
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

- [ ] **Step 2: Commit the whole role + templates + tasks.yml from Task 4**

  ```bash
  git add roles/vaultwarden/tasks/tasks.yml roles/vaultwarden/templates/
  git commit -m "feat(vaultwarden): add role tasks, docker-compose and nginx templates"
  ```

---

## Task 7: Create `playbooks/vaultwarden.yaml`

**Files:**
- Create: `playbooks/vaultwarden.yaml`

- [ ] **Step 1: Write the playbook**

  ```yaml
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

- [ ] **Step 2: Syntax-check the playbook**

  ```bash
  ansible-playbook --syntax-check playbooks/vaultwarden.yaml
  ```

  Expected: `playbook: playbooks/vaultwarden.yaml` (no errors).

- [ ] **Step 3: Commit**

  ```bash
  git add playbooks/vaultwarden.yaml
  git commit -m "feat(vaultwarden): add playbook"
  ```

---

## Task 8: Apply the playbook against the backend host (first pass — self-signed cert)

This is the first apply. SSL will be the self-signed cert because no LE cert exists yet. The host is not yet reachable from the Internet (proxy still untouched), but `192.168.0.161` must be reachable from where Ansible runs.

**Files:** none (state change on host)

- [ ] **Step 1: Dry-run**

  ```bash
  ansible-playbook playbooks/vaultwarden.yaml --check --diff
  ```

  Expected: most tasks show `changed` or `ok`; no fatal errors. (The `docker_compose_v2` task may show `skipped` or `changed` depending on check-mode support — acceptable.)

- [ ] **Step 2: Apply**

  ```bash
  ansible-playbook playbooks/vaultwarden.yaml
  ```

  Expected: `failed=0`. The recap line should show changed for nginx, file, copy, template, and the compose start.

- [ ] **Step 3: Confirm vaultwarden container is up**

  From the local machine:

  ```bash
  ansible vaultwarden -m shell -a 'docker ps --format "{{ "{{" }}.Names{{ "}}" }} {{ "{{" }}.Status{{ "}}" }}"'
  ```

  Expected: a line containing `vaultwarden` and `Up`.

- [ ] **Step 4: Confirm vaultwarden answers on localhost**

  ```bash
  ansible vaultwarden -m shell -a 'curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/alive'
  ```

  Expected: `200`.

- [ ] **Step 5: Confirm nginx serves HTTPS (self-signed) on the private IP**

  ```bash
  ansible vaultwarden -m shell -a 'curl -k --resolve vaultwarden.zozoh.fr:443:192.168.0.161 https://vaultwarden.zozoh.fr/alive --haproxy-protocol -sS -o /dev/null -w "%{http_code}\n"'
  ```

  Expected: `200`. (`--haproxy-protocol` makes curl send a PROXY-protocol header so nginx accepts the connection.)

  If `--haproxy-protocol` is unavailable, skip this step — Task 12 will validate via the real proxy path.

  *No commit — this task only mutates remote state.*

---

## Task 9: Update `inventories/group_vars/common.yaml` — SNI map + port-80 vhost

**Files:**
- Modify: `inventories/group_vars/common.yaml`

- [ ] **Step 1: Add the SNI map entry**

  Find the block (around line 75 in the current file):

  ```nginx
        map $ssl_preread_server_name $targetBackend {
          hostnames;

          gitlab.zozoh.fr 127.0.0.1:4443;
  ```

  Append `vaultwarden.zozoh.fr 192.168.0.161:443;` so the relevant section reads:

  ```nginx
        map $ssl_preread_server_name $targetBackend {
          hostnames;

          gitlab.zozoh.fr 127.0.0.1:4443;
          registry.zozoh.fr 127.0.0.1:4443;
          pages.zozoh.fr 127.0.0.1:4443;
          enzo.pages.zozoh.fr 127.0.0.1:4443;
          lagabelle-saint-hilaire.fr 127.0.0.1:4443;
          media.lagabelle-saint-hilaire.fr 127.0.0.1:4443;

          cloud.zozoh.fr 192.168.0.98:443;
          collaboraonline.zozoh.fr 192.168.0.98:443;

          vaultwarden.zozoh.fr 192.168.0.161:443;

          autodiscover.lagabelle-saint-hilaire.fr 192.168.0.97:443;
          autoconfig.lagabelle-saint-hilaire.fr 192.168.0.97:443;
          .zozoh.fr 192.168.0.97:443;
        }
  ```

  **Important:** the `.zozoh.fr` wildcard at the bottom catches everything not previously listed and sends it to the mail server. Our `vaultwarden.zozoh.fr` entry must appear **before** the `.zozoh.fr` wildcard. (`hostnames` maps prefer the most specific match, so order is not strictly required — but we keep it above the wildcard for clarity, matching the existing pattern.)

- [ ] **Step 2: Add the port-80 vhost**

  Add the following entry inside `nginx_vhosts:`. Place it after the `cloud.zozoh.fr` port-80 entry (around line 264) for readability:

  ```yaml
    - listen: "80"
      server_name: "vaultwarden.zozoh.fr"
      filename: "vaultwarden.zozoh.fr-80.conf"
      extra_parameters: |
        location / {
          gzip                    off;

          {{ '{{' }} _nginx_proxy_headers | indent(8) {{ '}}' }}

          proxy_pass http://192.168.0.161;
        }
  ```

  **CRITICAL:** when adding this entry, the file is itself a Jinja/YAML data file consumed by Ansible. The `{{ _nginx_proxy_headers | indent(8) }}` reference must appear **literally** in the YAML (Ansible renders it later when generating nginx.conf). Match the existing entries exactly (no escaping). The escaped form shown above is purely for the markdown display — in the actual file write:

  ```yaml
    - listen: "80"
      server_name: "vaultwarden.zozoh.fr"
      filename: "vaultwarden.zozoh.fr-80.conf"
      extra_parameters: |
        location / {
          gzip                    off;

          {{ _nginx_proxy_headers | indent(8) }}

          proxy_pass http://192.168.0.161;
        }
  ```

- [ ] **Step 3: Validate YAML and confirm the syntax**

  ```bash
  python3 -c "import yaml; yaml.safe_load(open('inventories/group_vars/common.yaml'))" && echo OK
  ansible-playbook --syntax-check playbooks/proxy.yaml
  ```

  Expected: `OK`, then `playbook: playbooks/proxy.yaml`.

- [ ] **Step 4: Commit**

  ```bash
  git add inventories/group_vars/common.yaml
  git commit -m "feat(proxy): route vaultwarden.zozoh.fr via SNI passthrough + HTTP redirect vhost"
  ```

---

## Task 10: Apply the proxy playbook

**Files:** none (state change on proxy host)

- [ ] **Step 1: Dry-run**

  ```bash
  ansible-playbook playbooks/proxy.yaml --check --diff
  ```

  Expected: diff shows the new SNI map line and the new `vaultwarden.zozoh.fr-80.conf` vhost.

- [ ] **Step 2: Apply**

  ```bash
  ansible-playbook playbooks/proxy.yaml
  ```

  Expected: `failed=0`. nginx reload handler runs.

- [ ] **Step 3: Confirm the SNI map line is on the proxy**

  ```bash
  ansible common_proxy -m shell -a 'grep -n vaultwarden /etc/nginx/nginx.conf'
  ```

  Expected: at least one line referencing `vaultwarden.zozoh.fr 192.168.0.161:443;`.

- [ ] **Step 4: Confirm port-80 reaches the backend**

  ```bash
  curl -sI http://vaultwarden.zozoh.fr/ | head
  ```

  Expected: `HTTP/1.1 301 Moved Permanently` with `Location: https://vaultwarden.zozoh.fr/...`. (The 301 comes from the backend's own nginx.)

- [ ] **Step 5: Confirm HTTPS reaches the backend (still self-signed)**

  ```bash
  curl -kI https://vaultwarden.zozoh.fr/ | head
  ```

  Expected: `HTTP/2 200` (or `HTTP/2 302` to `/#/...`). With `-k`, the self-signed cert is accepted.

---

## Task 11: Issue the Let's Encrypt certificate on the vaultwarden host

**Files:** none (state change on backend)

- [ ] **Step 1: Verify ACME challenge path works**

  ```bash
  ansible vaultwarden -m shell -a 'echo HELLO > /var/www/certbot/.well-known/acme-challenge/test && chmod 644 /var/www/certbot/.well-known/acme-challenge/test'
  curl -s http://vaultwarden.zozoh.fr/.well-known/acme-challenge/test
  ansible vaultwarden -m shell -a 'rm /var/www/certbot/.well-known/acme-challenge/test'
  ```

  Expected: curl prints `HELLO`. (You may need to `mkdir -p /var/www/certbot/.well-known/acme-challenge` first.)

- [ ] **Step 2: Issue the certificate**

  ```bash
  ansible vaultwarden -m shell -a 'certbot certonly --webroot -w /var/www/certbot -d vaultwarden.zozoh.fr --non-interactive --agree-tos --email enzo@zozoh.fr'
  ```

  Expected: `Successfully received certificate.` and a path under `/etc/letsencrypt/live/vaultwarden.zozoh.fr/`.

  (`enzo@zozoh.fr` is the value of `admin_email` defined in `inventories/group_vars/all.yaml`, used by the existing `certbot_admin_email`.)

- [ ] **Step 3: Re-run the vaultwarden playbook so it picks up the LE cert path**

  ```bash
  ansible-playbook playbooks/vaultwarden.yaml
  ```

  Expected: nginx vhost regenerated to point at `/etc/letsencrypt/live/...`, nginx restart handler fires, `failed=0`.

- [ ] **Step 4: Confirm the live cert is now LE-issued**

  ```bash
  echo | openssl s_client -connect vaultwarden.zozoh.fr:443 -servername vaultwarden.zozoh.fr 2>/dev/null | openssl x509 -noout -issuer
  ```

  Expected: `issuer=C=US, O=Let's Encrypt, CN=...`.

---

## Task 12: End-to-end verification

**Files:** none

- [ ] **Step 1: Web vault loads**

  Open `https://vaultwarden.zozoh.fr/` in a browser. Expected: the Bitwarden web vault login page renders without certificate warnings.

- [ ] **Step 2: Admin page accessible**

  Open `https://vaultwarden.zozoh.fr/admin`. Enter the **plaintext** admin passphrase (the input you fed to `vaultwarden hash` in Task 0, not the hash itself). Expected: admin dashboard loads.

- [ ] **Step 3: Invite a user**

  From `/admin → Users → Invite User`, enter your own email. Expected: email arrives (check the mailbox) with a link to register.

- [ ] **Step 4: WebSocket sync**

  Sign up via the invite link, then log in with the browser extension (or another browser tab). Make a vault change in one client. Expected: the second client receives the update within a couple of seconds (validates the `/notifications/hub` upgrade path).

- [ ] **Step 5: Re-apply confirms idempotency**

  ```bash
  ansible-playbook playbooks/vaultwarden.yaml
  ```

  Expected: `changed=0` (or `1` if docker pulled a newer image), no errors.

- [ ] **Step 6: Final commit hygiene**

  ```bash
  git status
  git log --oneline -10
  ```

  Expected: working tree clean; recent commits show the vaultwarden role, vault, playbook, and proxy update separately.

---

## Rollback notes

If anything goes wrong mid-deploy:

- **Container won't start:** `ansible vaultwarden -m shell -a 'cd /opt/vaultwarden && docker compose logs --tail=100 vaultwarden'`
- **nginx config bad:** `ansible vaultwarden -m shell -a 'nginx -t'`. Revert the role commit and re-apply.
- **SNI map broken on proxy:** revert the `common.yaml` commit and re-run `proxy.yaml`. The proxy never loses traffic for existing services because the map only added one entry.
- **LE rate-limit hit:** wait or use `--staging` first by adding `--test-cert` to the certbot command in Task 11 step 2; remove it and re-issue once confirmed.

## Spec coverage check

| Spec section                  | Implemented by |
|-------------------------------|----------------|
| Files to create               | Tasks 1–7      |
| Files to modify (common.yaml) | Task 9         |
| Variables (vars.yaml)         | Task 1         |
| Variables (vault.yaml)        | Task 2         |
| Role defaults/handlers/main   | Task 3         |
| Role task order               | Task 4         |
| docker-compose.yml template   | Task 5         |
| nginx vhost template          | Task 6         |
| Playbook                      | Task 7         |
| Proxy SNI map + port-80 vhost | Task 9         |
| Operator pre-flight           | Task 0         |
| LE cert issuance              | Task 11        |
| Verification plan             | Task 12        |
