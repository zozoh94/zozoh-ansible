# Mailserver Autoconfig / Autodiscover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the `mailserver` Ansible role to serve Thunderbird autoconfig XML, Outlook POX autodiscover XML, and RFC 6186 SRV records for every domain in `mailserver_domains`, backed by DNS-01 wildcard certificates issued via the OVH API.

**Architecture:** Static XML rendered at deploy time and served by a single nginx vhost that maps each `Host` header to a per-domain webroot and TLS cert. Certbot switches from HTTP-01 `--standalone` to DNS-01 via `python3-certbot-dns-ovh` and issues one wildcard cert per primary domain. The same `ovh_dns.py` driver that already provisions MX/SPF/DKIM gains `autodiscover` CNAME records and SRV records.

**Tech Stack:** Ansible, nginx, certbot + certbot-dns-ovh, Postfix `tls_server_sni_maps`, Dovecot `local_name`, Jinja2 templates, OVH REST API (python `ovh` client).

**Companion spec:** `docs/superpowers/specs/2026-05-19-mailserver-autoconfig-design.md`

---

## File Structure

**Created:**
- `roles/mailserver/tasks/autoconfig.yml` — nginx vhost + XML rendering task block
- `roles/mailserver/templates/autoconfig/mail-autoconfig.conf.j2` — nginx vhost
- `roles/mailserver/templates/autoconfig/config-v1.1.xml.j2` — Thunderbird XML
- `roles/mailserver/templates/autoconfig/autodiscover.xml.j2` — Outlook XML
- `roles/mailserver/templates/letsencrypt/ovh.ini.j2` — OVH credentials for certbot DNS plugin
- `roles/mailserver/files/letsencrypt-deploy-hook.sh` — service reload on cert renewal
- `roles/mailserver/templates/postfix/sni_map.j2` — Postfix SNI map source file

**Modified:**
- `roles/mailserver/defaults/main.yml` — new variables
- `roles/mailserver/tasks/packages.yml` — add `nginx`, `python3-certbot-dns-ovh`
- `roles/mailserver/tasks/certbot.yml` — DNS-01 branch + wildcard issuance
- `roles/mailserver/tasks/tasks.yml` — include `autoconfig.yml`
- `roles/mailserver/tasks/postfix.yml` — render sni_map, run `postmap -F`
- `roles/mailserver/templates/postfix/main.cf.j2` — `tls_server_sni_maps` line
- `roles/mailserver/templates/dovecot/conf.d/10-ssl.conf.j2` — `local_name` blocks
- `roles/mailserver/files/ovh_dns.py` — autodiscover CNAME + SRV records
- `roles/mailserver/handlers/main.yml` — nginx reload/restart, postmap sni
- `inventories/group_vars/mailserver/vars.yaml` — cutover to `dns-ovh` challenge

---

## Task 1: Add role default variables

**Files:**
- Modify: `roles/mailserver/defaults/main.yml`

- [ ] **Step 1: Append new defaults**

Append at the end of `roles/mailserver/defaults/main.yml`:

```yaml
# ─── Autoconfig / Autodiscover ─────────────────────────────────────────────────
# Serves Thunderbird (autoconfig.<domain>/mail/config-v1.1.xml) and Outlook
# (autodiscover.<domain>/autodiscover/autodiscover.xml) for every domain in
# mailserver_domains. Static XML, no PHP/FPM.
mailserver_autoconfig_enabled: true
# Host advertised in the XML and SRV records. MUST be covered by the TLS cert
# the mail services use. Default = mail.<primary_domain>, which the per-domain
# wildcard cert covers.
mailserver_autoconfig_advertised_host: "mail.{{ mailserver_domain }}"

# ─── Certbot challenge ─────────────────────────────────────────────────────────
# 'standalone' = legacy HTTP-01 path (port 80, must be free).
# 'dns-ovh'    = DNS-01 via OVH API. Requires python3-certbot-dns-ovh and the
#                same vault_mailserver_ovh_* credentials the DNS automation uses.
# Switch to 'dns-ovh' once OVH credentials are in vault and the role has run
# once successfully with the new package installed.
mailserver_certbot_challenge: standalone
# When true (with dns-ovh) issues one wildcard cert per entry in
# mailserver_domains: -d <d> -d *.<d>. Required for autoconfig/autodiscover
# SANs to be covered without enumerating each one.
mailserver_certbot_wildcards: false
mailserver_certbot_dns_propagation_seconds: 60
```

- [ ] **Step 2: Lint**

Run: `ansible-lint roles/mailserver/defaults/main.yml`
Expected: no errors (warnings about line length are pre-existing).

- [ ] **Step 3: Commit**

```bash
git add roles/mailserver/defaults/main.yml
git commit -m "feat(mailserver): add autoconfig + certbot challenge variables

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Add required packages

**Files:**
- Modify: `roles/mailserver/tasks/packages.yml`

- [ ] **Step 1: Add nginx + certbot-dns-ovh to the apt list**

Edit `roles/mailserver/tasks/packages.yml`. In the `Install mail server packages` task's `name:` list, insert two entries after `certbot`:

```yaml
      - certbot
      - python3-certbot-dns-ovh
      - nginx
      - python3-psycopg2
```

(Result: `python3-certbot-dns-ovh` and `nginx` are added; the rest of the list is unchanged.)

- [ ] **Step 2: Syntax-check the playbook**

Run: `ansible-playbook --syntax-check playbooks/mail.yaml -i inventories/all.yaml`
Expected: `playbook: playbooks/mail.yaml` with no parse errors.

- [ ] **Step 3: Commit**

```bash
git add roles/mailserver/tasks/packages.yml
git commit -m "feat(mailserver): install nginx and certbot-dns-ovh

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Render OVH credentials for certbot DNS-01

**Files:**
- Create: `roles/mailserver/templates/letsencrypt/ovh.ini.j2`
- Modify: `roles/mailserver/tasks/certbot.yml`

- [ ] **Step 1: Create the credentials template**

Create `roles/mailserver/templates/letsencrypt/ovh.ini.j2`:

```ini
# Managed by Ansible — do not edit by hand.
# Used by certbot-dns-ovh for DNS-01 challenges.
dns_ovh_endpoint = {{ mailserver_ovh_endpoint }}
dns_ovh_application_key = {{ mailserver_ovh_application_key }}
dns_ovh_application_secret = {{ mailserver_ovh_application_secret }}
dns_ovh_consumer_key = {{ mailserver_ovh_consumer_key }}
```

- [ ] **Step 2: Add the render task to certbot.yml**

Open `roles/mailserver/tasks/certbot.yml`. Insert this task at the top of the file, between `---` and the existing `Check if TLS certificate exists` task:

```yaml
- name: Render OVH API credentials for certbot DNS-01
  ansible.builtin.template:
    src: letsencrypt/ovh.ini.j2
    dest: /etc/letsencrypt/ovh.ini
    owner: root
    group: root
    mode: "0600"
  when: mailserver_certbot_challenge == "dns-ovh"
  no_log: true
```

The full file now starts with:

```yaml
---
- name: Render OVH API credentials for certbot DNS-01
  ansible.builtin.template:
    src: letsencrypt/ovh.ini.j2
    dest: /etc/letsencrypt/ovh.ini
    owner: root
    group: root
    mode: "0600"
  when: mailserver_certbot_challenge == "dns-ovh"
  no_log: true

- name: Check if TLS certificate exists
  ansible.builtin.stat:
    path: "{{ mailserver_tls_cert }}"
  register: tls_cert_stat
...
```

- [ ] **Step 3: Lint and syntax check**

Run: `ansible-lint roles/mailserver/tasks/certbot.yml && ansible-playbook --syntax-check playbooks/mail.yaml -i inventories/all.yaml`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add roles/mailserver/templates/letsencrypt/ovh.ini.j2 roles/mailserver/tasks/certbot.yml
git commit -m "feat(mailserver): render OVH credentials for certbot DNS-01

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Refactor certbot.yml — DNS-01 wildcard issuance

**Files:**
- Modify: `roles/mailserver/tasks/certbot.yml`

- [ ] **Step 1: Replace the cert-issuing task with a branched version**

Replace the existing `Obtain Let's Encrypt certificate` task (currently at lines 7-16 — the single `certbot certonly --standalone ...` call) with this two-task block, keeping the surrounding `Check if TLS certificate exists` and `Set up certbot auto-renewal cron` tasks intact:

```yaml
- name: Obtain Let's Encrypt certificate (HTTP-01 standalone)
  ansible.builtin.command: >
    certbot certonly --standalone --non-interactive --agree-tos
    --email {{ mailserver_certbot_email }}
    --cert-name {{ mailserver_domain }}
    -d {{ mailserver_hostname }}
    {% for d in mailserver_certbot_extra_domains %}-d {{ d }} {% endfor %}
  when:
    - mailserver_certbot_challenge == "standalone"
    - not tls_cert_stat.stat.exists
  register: certbot_http01_result
  changed_when: "'Successfully received certificate' in certbot_http01_result.stdout"

- name: Obtain Let's Encrypt wildcard certificate (DNS-01 via OVH)
  ansible.builtin.command: >
    certbot certonly --non-interactive --agree-tos
    --email {{ mailserver_certbot_email }}
    --dns-ovh --dns-ovh-credentials /etc/letsencrypt/ovh.ini
    --dns-ovh-propagation-seconds {{ mailserver_certbot_dns_propagation_seconds }}
    --cert-name {{ item }}
    -d {{ item }} -d *.{{ item }}
  loop: "{{ mailserver_domains }}"
  when: mailserver_certbot_challenge == "dns-ovh"
  register: certbot_dns01_result
  changed_when: "'Successfully received certificate' in certbot_dns01_result.stdout"
```

Notes:
- HTTP-01 task is gated on `not tls_cert_stat.stat.exists` to preserve current behavior (skip if already issued). DNS-01 task is **not** gated on `stat`, because certbot itself is idempotent for `--cert-name <d>` (it returns "Certificate not yet due for renewal" without changing anything).
- DNS-01 issues per-domain wildcards: `zozoh.fr` + `*.zozoh.fr`, and `lagabelle-saint-hilaire.fr` + `*.lagabelle-saint-hilaire.fr`.

- [ ] **Step 2: Update the renewal cron to support DNS-01 with no service interruption**

Replace the `Set up certbot auto-renewal cron` task at the bottom of `roles/mailserver/tasks/certbot.yml`. The new job uses a deploy hook (rendered in Task 5) instead of an inline `--post-hook`:

```yaml
- name: Set up certbot auto-renewal cron
  ansible.builtin.cron:
    name: "certbot renewal"
    minute: "0"
    hour: "3"
    job: "certbot renew --quiet"
    user: root
```

The deploy hook installed in Task 5 reloads services only when a cert was actually replaced — cleaner than the previous unconditional post-hook.

- [ ] **Step 3: Syntax check**

Run: `ansible-playbook --syntax-check playbooks/mail.yaml -i inventories/all.yaml`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add roles/mailserver/tasks/certbot.yml
git commit -m "feat(mailserver): add DNS-01 wildcard branch to certbot task

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Install certbot deploy hook for service reloads

**Files:**
- Create: `roles/mailserver/files/letsencrypt-deploy-hook.sh`
- Modify: `roles/mailserver/tasks/certbot.yml`

- [ ] **Step 1: Create the deploy hook script**

Create `roles/mailserver/files/letsencrypt-deploy-hook.sh` with mode 0755:

```bash
#!/bin/sh
# Managed by Ansible — runs after a successful certbot renewal that produced
# a new certificate. RENEWED_LINEAGE points to /etc/letsencrypt/live/<name>.
# We reload every service that may serve the new cert. Failures here MUST
# bubble up so certbot reports them.
set -eu

systemctl reload nginx    || true   # nginx may not be installed yet on first run
systemctl reload postfix
systemctl reload dovecot

# Rebuild postfix SNI map: postmap -F embeds the cert/key contents in the
# .db, so the .db must be regenerated when the underlying files change.
if [ -f /etc/postfix/sni_map ]; then
    postmap -F hash:/etc/postfix/sni_map
    systemctl reload postfix
fi
```

The `|| true` on nginx covers first-deploy ordering (cert issuance can happen before `autoconfig.yml` installs nginx). All later renewals reach the working nginx.

- [ ] **Step 2: Add a task to install the hook**

Append to `roles/mailserver/tasks/certbot.yml` (after the renewal cron task):

```yaml
- name: Install certbot deploy hook (reload services on renewal)
  ansible.builtin.copy:
    src: letsencrypt-deploy-hook.sh
    dest: /etc/letsencrypt/renewal-hooks/deploy/zozoh-reload.sh
    owner: root
    group: root
    mode: "0755"
```

- [ ] **Step 3: Lint**

Run: `ansible-lint roles/mailserver/tasks/certbot.yml`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add roles/mailserver/files/letsencrypt-deploy-hook.sh roles/mailserver/tasks/certbot.yml
git commit -m "feat(mailserver): add certbot deploy hook for service reloads

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Add per-domain SNI to Dovecot and Postfix

Mail clients connecting to legacy `imap.<domain>` / `smtp.<domain>` CNAMEs (still emitted by `ovh_dns.py`) must receive a cert whose SAN matches the SNI they sent. With per-domain wildcard certs we need SNI selection in both daemons.

**Files:**
- Modify: `roles/mailserver/templates/dovecot/conf.d/10-ssl.conf.j2`
- Create: `roles/mailserver/templates/postfix/sni_map.j2`
- Modify: `roles/mailserver/templates/postfix/main.cf.j2`
- Modify: `roles/mailserver/tasks/postfix.yml`
- Modify: `roles/mailserver/handlers/main.yml`

- [ ] **Step 1: Add local_name blocks to dovecot 10-ssl.conf**

Replace the entire content of `roles/mailserver/templates/dovecot/conf.d/10-ssl.conf.j2` with:

```jinja
ssl = required
ssl_cert = <{{ mailserver_tls_fullchain }}
ssl_key = <{{ mailserver_tls_key }}
ssl_dh = </usr/share/dovecot/dh.pem
ssl_min_protocol = TLSv1.2
ssl_prefer_server_ciphers = yes
ssl_cipher_list = ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256

{% if mailserver_certbot_challenge == "dns-ovh" %}
# Per-domain SNI: dovecot picks the right wildcard cert for each requested SNI
# name. The default cert (above) is mailserver_domain's wildcard.
{% for d in mailserver_domains if d != mailserver_domain %}
local_name {{ d }} {
    ssl_cert = </etc/letsencrypt/live/{{ d }}/fullchain.pem
    ssl_key  = </etc/letsencrypt/live/{{ d }}/privkey.pem
}
local_name *.{{ d }} {
    ssl_cert = </etc/letsencrypt/live/{{ d }}/fullchain.pem
    ssl_key  = </etc/letsencrypt/live/{{ d }}/privkey.pem
}
{% endfor %}
{% endif %}
```

- [ ] **Step 2: Create the Postfix SNI map template**

Create `roles/mailserver/templates/postfix/sni_map.j2`:

```jinja
# Managed by Ansible — Postfix tls_server_sni_maps source.
# Format: <name> <key file> <chain file>
# After editing, regenerate the .db with: postmap -F hash:/etc/postfix/sni_map
{% if mailserver_certbot_challenge == "dns-ovh" %}
{% for d in mailserver_domains %}
{{ d }} /etc/letsencrypt/live/{{ d }}/privkey.pem /etc/letsencrypt/live/{{ d }}/fullchain.pem
*.{{ d }} /etc/letsencrypt/live/{{ d }}/privkey.pem /etc/letsencrypt/live/{{ d }}/fullchain.pem
{% endfor %}
{% endif %}
```

- [ ] **Step 3: Reference the SNI map in main.cf.j2**

In `roles/mailserver/templates/postfix/main.cf.j2`, immediately after line 55 (`smtpd_tls_key_file ...`), insert:

```jinja
{% if mailserver_certbot_challenge == "dns-ovh" %}
tls_server_sni_maps           = hash:/etc/postfix/sni_map
{% endif %}
```

- [ ] **Step 4: Add render + postmap tasks to postfix.yml**

Open `roles/mailserver/tasks/postfix.yml`. Find the existing block that renders `/etc/postfix/main.cf` (likely a `template:` task referencing `postfix/main.cf.j2`). Immediately after that task, insert:

```yaml
- name: Render Postfix SNI map
  ansible.builtin.template:
    src: postfix/sni_map.j2
    dest: /etc/postfix/sni_map
    owner: root
    group: root
    mode: "0644"
  when: mailserver_certbot_challenge == "dns-ovh"
  notify: Rebuild postfix sni map

- name: Ensure SNI map is built (initial deploy)
  ansible.builtin.command: postmap -F hash:/etc/postfix/sni_map
  args:
    creates: /etc/postfix/sni_map.db
  when: mailserver_certbot_challenge == "dns-ovh"
```

- [ ] **Step 5: Add the `Rebuild postfix sni map` handler**

Append to `roles/mailserver/handlers/main.yml`:

```yaml
- name: Rebuild postfix sni map
  ansible.builtin.command: postmap -F hash:/etc/postfix/sni_map
  changed_when: true
  notify: Reload postfix
```

- [ ] **Step 6: Lint + syntax check + render-diff dry-run**

Run:

```bash
ansible-lint roles/mailserver/
ansible-playbook --syntax-check playbooks/mail.yaml -i inventories/all.yaml
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add roles/mailserver/templates/dovecot/conf.d/10-ssl.conf.j2 \
        roles/mailserver/templates/postfix/sni_map.j2 \
        roles/mailserver/templates/postfix/main.cf.j2 \
        roles/mailserver/tasks/postfix.yml \
        roles/mailserver/handlers/main.yml
git commit -m "feat(mailserver): per-domain SNI for dovecot and postfix

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Extend ovh_dns.py with autodiscover CNAME and SRV records

**Files:**
- Modify: `roles/mailserver/files/ovh_dns.py`

The existing script already creates an `autoconfig` CNAME (lines 222-226). We add `autodiscover` (same shape) and five SRV records (two positive, three negative).

- [ ] **Step 1: Add an SRV helper alongside `ensure_record`**

In `roles/mailserver/files/ovh_dns.py`, insert this helper immediately after the `ensure_mx_record` function (around line 129):

```python
def ensure_srv_record(client, zone, subdomain, priority, weight, port, target, ttl=3600):
    """Ensure an SRV record exists. RFC 2782 format: 'priority weight port target.'.
    Negative record per RFC 6186: priority=0, weight=0, port=0, target='.' (single dot)."""
    global changed
    if target == ".":
        srv_target = "0 0 0 ."
    else:
        srv_target = f"{priority} {weight} {port} {target}."
    existing = find_existing_record(client, zone, subdomain, "SRV")
    for rec in existing:
        if rec["target"] == srv_target:
            print(f"  OK: SRV {subdomain}.{zone} -> {srv_target}")
            return
        # An SRV with the same name but different value: leave it alone and
        # log; if the user has an override they keep it. Idempotency favors
        # not deleting records we did not create.
    api_call_with_retry(
        client.post,
        f"/domain/zone/{zone}/record",
        fieldType="SRV",
        subDomain=subdomain,
        target=srv_target,
        ttl=ttl,
    )
    print(f"  CREATED: SRV {subdomain}.{zone} -> {srv_target}")
    changed = True
```

- [ ] **Step 2: Emit `autodiscover` CNAME and SRV records inside the per-domain loop**

In the `for domain in domains:` block, locate the `# Autoconfig record (for Thunderbird and other clients)` section (lines 222-226). Replace those five lines with:

```python
            # Autoconfig / Autodiscover CNAMEs — both point at the mail host so
            # nginx can serve per-Host XML files for Thunderbird and Outlook.
            for alias in ("autoconfig", "autodiscover"):
                alias_subdomain = alias
                if subdomain:
                    alias_subdomain = f"{alias}.{subdomain}"
                ensure_record(client, zone, alias_subdomain, "CNAME", f"{hostname}.")

            # RFC 6186 SRV records — modern clients (Thunderbird, Apple Mail,
            # K-9) use these instead of the HTTP discovery URLs.
            srv_target_host = os.environ.get("MAIL_SRV_TARGET", hostname)
            srv_base = subdomain if subdomain else None

            def srv_sub(name):
                return f"{name}.{subdomain}" if subdomain else name

            # Positive: IMAPS:993, Submission:587 STARTTLS.
            ensure_srv_record(client, zone, srv_sub("_imaps._tcp"),
                              0, 1, 993, srv_target_host)
            ensure_srv_record(client, zone, srv_sub("_submission._tcp"),
                              0, 1, 587, srv_target_host)
            # Negative: explicitly tell clients NOT to use these protocols.
            for negative in ("_imap._tcp", "_pop3._tcp", "_pop3s._tcp"):
                ensure_srv_record(client, zone, srv_sub(negative),
                                  0, 0, 0, ".")
```

The `MAIL_SRV_TARGET` env var (added in Task 8 below) lets the Ansible side override the SRV target — we want all SRV records to point at `mail.<primary_domain>` regardless of which zone they live in.

- [ ] **Step 3: Manual smoke — confirm the script parses**

Run: `python3 -c "import ast; ast.parse(open('roles/mailserver/files/ovh_dns.py').read())"`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add roles/mailserver/files/ovh_dns.py
git commit -m "feat(mailserver): emit autodiscover CNAME and RFC 6186 SRV records

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Pass `MAIL_SRV_TARGET` from Ansible to ovh_dns.py

**Files:**
- Modify: `roles/mailserver/tasks/ovh_dns.yml`

- [ ] **Step 1: Add MAIL_SRV_TARGET to the environment block**

In `roles/mailserver/tasks/ovh_dns.yml`, in the `Create OVH DNS records for each domain` task's `environment:` map (currently lines 26-41), insert one new line after `MAIL_MX_HOST`:

```yaml
    MAIL_HOSTNAME: "{{ mailserver_hostname }}"
    MAIL_MX_HOST: "{{ mailserver_mx_host }}"
    MAIL_SRV_TARGET: "{{ mailserver_autoconfig_advertised_host }}"
    MAIL_PUBLIC_IP: "{{ _mailserver_public_ip }}"
```

- [ ] **Step 2: Syntax check**

Run: `ansible-playbook --syntax-check playbooks/mail.yaml -i inventories/all.yaml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/mailserver/tasks/ovh_dns.yml
git commit -m "feat(mailserver): pass MAIL_SRV_TARGET to ovh_dns.py

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Create the nginx vhost template

**Files:**
- Create: `roles/mailserver/templates/autoconfig/mail-autoconfig.conf.j2`

- [ ] **Step 1: Write the vhost template**

Create `roles/mailserver/templates/autoconfig/mail-autoconfig.conf.j2`:

```nginx
# Managed by Ansible — do not edit by hand.
# Serves Thunderbird autoconfig + Outlook autodiscover XML for every domain
# in mailserver_domains.

map $host $autoconfig_cert {
{% for d in mailserver_domains %}
    ~*\.{{ d | regex_escape }}$  {{ d }};
{% endfor %}
    default {{ mailserver_domain }};
}

server {
    listen 80;
    listen [::]:80;
    server_name
{% for d in mailserver_domains %}
        autoconfig.{{ d }}
        autodiscover.{{ d }}
{% endfor %}
    ;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name
{% for d in mailserver_domains %}
        autoconfig.{{ d }}
        autodiscover.{{ d }}
{% endfor %}
    ;

    ssl_certificate     /etc/letsencrypt/live/$autoconfig_cert/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$autoconfig_cert/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    access_log /var/log/nginx/autoconfig_access.log;
    error_log  /var/log/nginx/autoconfig_error.log;

    root /var/www/autoconfig/$host;

    # Thunderbird
    location = /mail/config-v1.1.xml {
        default_type application/xml;
    }

    # Outlook (POX). nginx serves the static file for any HTTP method.
    location = /autodiscover/autodiscover.xml {
        default_type application/xml;
    }
    # Outlook capitalization variants — rewrite to the canonical path.
    location ~* ^/Autodiscover/Autodiscover\.xml$ {
        rewrite ^ /autodiscover/autodiscover.xml last;
    }

    location / { return 404; }
}
```

`regex_escape` is the built-in Ansible/Jinja filter for escaping regex metacharacters.

- [ ] **Step 2: Manual render check**

There is no test render step — the template is rendered by Ansible at deploy time and validated by `nginx -t` after install (Task 12).

- [ ] **Step 3: Commit**

```bash
git add roles/mailserver/templates/autoconfig/mail-autoconfig.conf.j2
git commit -m "feat(mailserver): add nginx autoconfig vhost template

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Create the Thunderbird and Outlook XML templates

**Files:**
- Create: `roles/mailserver/templates/autoconfig/config-v1.1.xml.j2`
- Create: `roles/mailserver/templates/autoconfig/autodiscover.xml.j2`

- [ ] **Step 1: Write the Thunderbird (Mozilla ISPDB) template**

Create `roles/mailserver/templates/autoconfig/config-v1.1.xml.j2`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<clientConfig version="1.1">
  <emailProvider id="{{ item }}">
    <domain>{{ item }}</domain>
    <displayName>{{ item }}</displayName>
    <displayShortName>{{ item }}</displayShortName>
    <incomingServer type="imap">
      <hostname>{{ mailserver_autoconfig_advertised_host }}</hostname>
      <port>993</port>
      <socketType>SSL</socketType>
      <username>%EMAILADDRESS%</username>
      <authentication>password-cleartext</authentication>
    </incomingServer>
    <outgoingServer type="smtp">
      <hostname>{{ mailserver_autoconfig_advertised_host }}</hostname>
      <port>587</port>
      <socketType>STARTTLS</socketType>
      <username>%EMAILADDRESS%</username>
      <authentication>password-cleartext</authentication>
    </outgoingServer>
  </emailProvider>
</clientConfig>
```

- [ ] **Step 2: Write the Outlook (POX autodiscover) template**

Create `roles/mailserver/templates/autoconfig/autodiscover.xml.j2`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<Autodiscover xmlns="http://schemas.microsoft.com/exchange/autodiscover/responseschema/2006">
  <Response xmlns="http://schemas.microsoft.com/exchange/autodiscover/outlook/responseschema/2006a">
    <Account>
      <AccountType>email</AccountType>
      <Action>settings</Action>
      <Protocol>
        <Type>IMAP</Type>
        <Server>{{ mailserver_autoconfig_advertised_host }}</Server>
        <Port>993</Port>
        <DomainRequired>off</DomainRequired>
        <LoginName></LoginName>
        <SPA>off</SPA>
        <SSL>on</SSL>
        <AuthRequired>on</AuthRequired>
      </Protocol>
      <Protocol>
        <Type>SMTP</Type>
        <Server>{{ mailserver_autoconfig_advertised_host }}</Server>
        <Port>587</Port>
        <DomainRequired>off</DomainRequired>
        <LoginName></LoginName>
        <SPA>off</SPA>
        <SSL>off</SSL>
        <Encryption>TLS</Encryption>
        <AuthRequired>on</AuthRequired>
        <UsePOPAuth>off</UsePOPAuth>
        <SMTPLast>off</SMTPLast>
      </Protocol>
    </Account>
  </Response>
</Autodiscover>
```

- [ ] **Step 3: Commit**

```bash
git add roles/mailserver/templates/autoconfig/
git commit -m "feat(mailserver): add Thunderbird and Outlook XML templates

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Add nginx handlers

**Files:**
- Modify: `roles/mailserver/handlers/main.yml`

- [ ] **Step 1: Append nginx handlers**

Append at the end of `roles/mailserver/handlers/main.yml`:

```yaml
- name: Reload nginx
  ansible.builtin.service:
    name: nginx
    state: reloaded

- name: Restart nginx
  ansible.builtin.service:
    name: nginx
    state: restarted
```

- [ ] **Step 2: Lint**

Run: `ansible-lint roles/mailserver/handlers/main.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/mailserver/handlers/main.yml
git commit -m "feat(mailserver): add nginx reload/restart handlers

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Create the autoconfig task file and wire it into the role

**Files:**
- Create: `roles/mailserver/tasks/autoconfig.yml`
- Modify: `roles/mailserver/tasks/tasks.yml`

- [ ] **Step 1: Create the autoconfig task file**

Create `roles/mailserver/tasks/autoconfig.yml`:

```yaml
---
# Renders the autoconfig nginx vhost and per-domain XML files.
# Runs after certbot.yml so the wildcard certs are in place.

- name: Disable default nginx site
  ansible.builtin.file:
    path: /etc/nginx/sites-enabled/default
    state: absent
  notify: Reload nginx

- name: Ensure /var/www/autoconfig exists
  ansible.builtin.file:
    path: /var/www/autoconfig
    state: directory
    owner: root
    group: root
    mode: "0755"

- name: Create per-domain webroot directories
  ansible.builtin.file:
    path: "/var/www/autoconfig/{{ item.0 }}.{{ item.1 }}/{{ item.2 }}"
    state: directory
    owner: root
    group: root
    mode: "0755"
  loop: "{{ ['autoconfig'] | product(mailserver_domains, ['mail']) | list
          + ['autodiscover'] | product(mailserver_domains, ['autodiscover']) | list }}"

- name: Render Thunderbird autoconfig XML per domain
  ansible.builtin.template:
    src: autoconfig/config-v1.1.xml.j2
    dest: "/var/www/autoconfig/autoconfig.{{ item }}/mail/config-v1.1.xml"
    owner: root
    group: root
    mode: "0644"
  loop: "{{ mailserver_domains }}"

- name: Render Outlook autodiscover XML per domain
  ansible.builtin.template:
    src: autoconfig/autodiscover.xml.j2
    dest: "/var/www/autoconfig/autodiscover.{{ item }}/autodiscover/autodiscover.xml"
    owner: root
    group: root
    mode: "0644"
  loop: "{{ mailserver_domains }}"

- name: Render nginx autoconfig vhost
  ansible.builtin.template:
    src: autoconfig/mail-autoconfig.conf.j2
    dest: /etc/nginx/sites-available/mail-autoconfig.conf
    owner: root
    group: root
    mode: "0644"
  notify: Reload nginx

- name: Enable nginx autoconfig vhost
  ansible.builtin.file:
    src: /etc/nginx/sites-available/mail-autoconfig.conf
    dest: /etc/nginx/sites-enabled/mail-autoconfig.conf
    state: link
  notify: Reload nginx

- name: Validate nginx config
  ansible.builtin.command: nginx -t
  changed_when: false

- name: Ensure nginx is enabled and running
  ansible.builtin.service:
    name: nginx
    state: started
    enabled: true
```

The `loop:` for per-domain dirs uses Jinja's `product` to expand to `(prefix, domain, subdir)` tuples:
- `('autoconfig', 'zozoh.fr', 'mail')` → `/var/www/autoconfig/autoconfig.zozoh.fr/mail`
- `('autodiscover', 'zozoh.fr', 'autodiscover')` → `/var/www/autoconfig/autodiscover.zozoh.fr/autodiscover`

- [ ] **Step 2: Wire the task file into tasks.yml**

In `roles/mailserver/tasks/tasks.yml`, between the existing `Configure SpamAssassin` task (line 32-33) and `Configure fail2ban` task (line 35-36), insert:

```yaml
- name: Configure mail autoconfig (Thunderbird, Outlook) and nginx vhost
  ansible.builtin.include_tasks: autoconfig.yml
  when: mailserver_autoconfig_enabled
```

- [ ] **Step 3: Lint + syntax check**

Run:

```bash
ansible-lint roles/mailserver/tasks/autoconfig.yml
ansible-playbook --syntax-check playbooks/mail.yaml -i inventories/all.yaml
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add roles/mailserver/tasks/autoconfig.yml roles/mailserver/tasks/tasks.yml
git commit -m "feat(mailserver): wire autoconfig nginx vhost + XML into role

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Cutover — flip the inventory to DNS-01

**Files:**
- Modify: `inventories/group_vars/mailserver/vars.yaml`

This task is the user-facing toggle. It is reversible by changing the variable back.

- [ ] **Step 1: Flip the challenge type in inventory**

In `inventories/group_vars/mailserver/vars.yaml`, replace the block that currently defines `mailserver_certbot_extra_domains` (lines 12-16) with:

```yaml
# DNS-01 wildcard issuance via OVH API — covers mail, autoconfig, autodiscover,
# imap, smtp, and any future *.zozoh.fr name without re-running SAN bookkeeping.
mailserver_certbot_challenge: dns-ovh
mailserver_certbot_wildcards: true
# Empty: under wildcard issuance, extra SANs are redundant. Left as a list for
# forward compatibility.
mailserver_certbot_extra_domains: []
```

- [ ] **Step 2: Render-only dry run**

Run:

```bash
ansible-playbook playbooks/mail.yaml \
    -i inventories/all.yaml \
    --limit zozoh.fr \
    --check --diff
```

Expected: clean exit; diff shows the new files (ovh.ini, sni_map, vhost, XML), the new packages, and the certbot DNS-01 task. **Do NOT yet apply.**

- [ ] **Step 3: Commit the inventory change**

```bash
git add inventories/group_vars/mailserver/vars.yaml
git commit -m "feat(mailserver): cutover to DNS-01 wildcard cert issuance

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: Deploy and verify end-to-end

This is the operational task that brings the feature live. Per the user's risky-ops pattern (see `~/.claude/projects/.../memory/risky-ops-pattern.md`), it runs against a Proxmox-snapshotted host first and is gated per-host with `--limit`.

**Files:** none modified — this is verification only.

- [ ] **Step 1: Snapshot the mailserver host**

Use whatever the user normally uses for `proxmox_snap`. Confirm the snapshot exists before proceeding.

- [ ] **Step 2: Deploy the role**

```bash
ansible-playbook playbooks/mail.yaml \
    -i inventories/all.yaml \
    --limit zozoh.fr \
    --diff
```

Expected: tasks for OVH credentials render, certbot DNS-01 issuance per domain, SNI map render + postmap, autoconfig.yml task block, ovh_dns.py extended record creation. No failed tasks.

- [ ] **Step 3: Verify DNS records were created**

```bash
dig +short autoconfig.zozoh.fr CNAME
# Expected: mail.zozoh.fr.

dig +short autodiscover.zozoh.fr CNAME
# Expected: mail.zozoh.fr.

dig +short _imaps._tcp.zozoh.fr SRV
# Expected: 0 1 993 mail.zozoh.fr.

dig +short _submission._tcp.zozoh.fr SRV
# Expected: 0 1 587 mail.zozoh.fr.

# Repeat for lagabelle-saint-hilaire.fr.
```

- [ ] **Step 4: Verify wildcard certs cover the SNI names**

```bash
echo | openssl s_client -servername autoconfig.zozoh.fr \
       -connect autoconfig.zozoh.fr:443 2>/dev/null \
  | openssl x509 -noout -subject -ext subjectAltName
# Expected: SAN list contains DNS:zozoh.fr and DNS:*.zozoh.fr.

echo | openssl s_client -servername autodiscover.lagabelle-saint-hilaire.fr \
       -connect autodiscover.lagabelle-saint-hilaire.fr:443 2>/dev/null \
  | openssl x509 -noout -subject -ext subjectAltName
# Expected: SAN list contains DNS:lagabelle-saint-hilaire.fr and DNS:*.lagabelle-saint-hilaire.fr.
```

- [ ] **Step 5: Verify XML endpoints return parseable XML**

```bash
curl -fsS https://autoconfig.zozoh.fr/mail/config-v1.1.xml | xmllint --noout -
# Expected: exit 0, no output.

curl -fsS -X POST https://autodiscover.zozoh.fr/autodiscover/autodiscover.xml \
     -H 'Content-Type: text/xml' --data '<x/>' | xmllint --noout -
# Expected: exit 0, no output.

# Repeat for autoconfig.lagabelle-saint-hilaire.fr.
```

Also visually inspect the rendered XML for the right `<hostname>mail.zozoh.fr</hostname>` value and the matching `<emailProvider id="...">`.

- [ ] **Step 6: Verify Postfix and Dovecot still serve TLS on submission/IMAPS**

```bash
echo | openssl s_client -servername mail.zozoh.fr -connect mail.zozoh.fr:993 -starttls "" 2>/dev/null \
  | openssl x509 -noout -subject -ext subjectAltName

echo | openssl s_client -servername mail.zozoh.fr -connect mail.zozoh.fr:587 -starttls smtp 2>/dev/null \
  | openssl x509 -noout -subject -ext subjectAltName

# Test the legacy CNAME path (backward compat):
echo | openssl s_client -servername imap.lagabelle-saint-hilaire.fr \
       -connect imap.lagabelle-saint-hilaire.fr:993 2>/dev/null \
  | openssl x509 -noout -subject -ext subjectAltName
# Expected: SAN includes DNS:*.lagabelle-saint-hilaire.fr (selected via Postfix/Dovecot SNI).
```

- [ ] **Step 7: Real-client smoke test**

In Thunderbird → "Set up another account" → enter `test@zozoh.fr` (any plausible address) → the wizard must auto-fill IMAP/SMTP fields with `mail.zozoh.fr` and 993/587 without manual editing. Repeat with `test@lagabelle-saint-hilaire.fr`.

- [ ] **Step 8: If all verifications pass, drop the snapshot**

(Or keep it for one renewal cycle to confirm `certbot renew` + deploy hook work — your call.)

---

## Out-of-scope reminders (from the spec)

- No webmail (`<documentation>` block omitted).
- No POP3 in either XML.
- No autodiscover v2 / JSON endpoint.
- No `.mobileconfig` for iOS — Apple Mail uses RFC 6186 SRV.
- No `_caldav`/`_carddav` SRV.
- No firewall (UFW) rules — the role does not manage host firewalls.

Backward compatibility: the `imap.<domain>` and `smtp.<domain>` CNAMEs that `ovh_dns.py` already creates are left in place. New account setups land on `mail.<primary_domain>` (advertised by XML + SRV); legacy cached configs keep working because Dovecot/Postfix now do per-domain SNI.
