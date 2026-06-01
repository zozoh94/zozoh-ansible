# Coolify behind nginx (proxy1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provision a `coolify` host running Coolify (installed via the official `install.sh`), with the dashboard at `https://coolify.zozoh.fr` and all Coolify-served sites fronted by the existing public nginx reverse proxy `proxy1`, using TLS passthrough so Coolify's internal Traefik terminates TLS and issues Let's Encrypt certs.

**Architecture:** proxy1 (nginx) stays the only public host. Its `stream` block `ssl_preread`-routes raw TLS on `:443` to `coolify:443` (with `proxy_protocol on`, shared by all passthrough backends); its `:80` vhost forwards http to `coolify:80` for ACME HTTP-01. A new `coolify` Ansible role runs `install.sh` idempotently and patches Coolify's generated Traefik proxy compose to trust proxy1's PROXY-protocol header on the `https` entrypoint.

**Tech Stack:** Ansible (ansible-core 2.14, run via `./venv/bin/ansible-playbook`), `geerlingguy.nginx` (proxy1 config rendered from `group_vars/common.yaml`), `community.docker.docker_compose_v2`, Coolify (Docker + Traefik).

**Spec:** `docs/superpowers/specs/2026-06-01-coolify-behind-nginx-design.md`

**Conventions (from project memory / existing code):**
- All `ansible*` binaries live in `./venv/bin/`. The repo is not on `$PATH`; always call `./venv/bin/ansible-playbook` etc.
- Managed hosts connect as `debian@<host>` + `sudo` (the playbook uses `become: yes`; `remote_user = debian` is set in `ansible.cfg`). Never `root@`.
- Inventory host FQDN is `coolify.ovh.zozoh.fr`; the inventory **group** is `coolify`. Use the group name for `group_vars/` and `--limit` should use the FQDN.
- Roles use an idempotency guard fact `role_<name>_played` (see `roles/vaultwarden`).

---

## File Structure

| File | Responsibility | Action |
| --- | --- | --- |
| `inventories/group_vars/coolify/vars.yaml` | Networks + host map + domain + proxy IP for the `coolify` group | Create |
| `playbooks/coolify.yaml` | Play binding the `coolify` group to `common` + `coolify` roles | Create |
| `roles/coolify/defaults/main.yml` | Role defaults (install URL, proxy compose path, guard fact) | Create |
| `roles/coolify/handlers/main.yml` | Restart the Coolify proxy (Traefik) | Create |
| `roles/coolify/tasks/main.yml` | Guarded include of `tasks.yml` | Create |
| `roles/coolify/tasks/tasks.yml` | Install Coolify + patch Traefik PROXY-protocol trust | Create |
| `inventories/group_vars/common.yaml` | proxy1 nginx: upstream + stream map + port-80 vhost for Coolify | Modify |

No change to `inventories/all.yaml` — the `coolify` host/group already exists (vmid 208).

---

## Task 1: Coolify group_vars

**Files:**
- Create: `inventories/group_vars/coolify/vars.yaml`

- [ ] **Step 1: Create the group_vars file**

```yaml
---
private_network: "192.168.0.192/27"
admin_network: "192.168.1.192/27"

group_hosts:
  coolify.ovh.zozoh.fr: 192.168.0.193

# Coolify dashboard domain (fronted by proxy1, TLS terminated by Coolify Traefik)
coolify_domain: coolify.zozoh.fr

# proxy1 — the only host trusted to send the PROXY protocol header on :443
reverse_proxy_ip: 192.168.0.225
```

- [ ] **Step 2: Verify the inventory parses and the host resolves these vars**

Run:
```bash
./venv/bin/ansible-inventory --host coolify.ovh.zozoh.fr
```
Expected: JSON output that includes `"coolify_domain": "coolify.zozoh.fr"`, `"private_network": "192.168.0.192/27"`, `"admin_network": "192.168.1.192/27"`, and `"reverse_proxy_ip": "192.168.0.225"`. No parse error.

- [ ] **Step 3: Verify the host sits in the `coolify` group**

Run:
```bash
./venv/bin/ansible-inventory --graph coolify
```
Expected: shows `@coolify:` with `coolify.ovh.zozoh.fr` under it.

- [ ] **Step 4: Commit**

```bash
git add inventories/group_vars/coolify/vars.yaml
git commit -m "feat(coolify): group_vars — networks, domain, reverse proxy IP"
```

---

## Task 2: Coolify role scaffolding (defaults, handlers, main)

Creates the role skeleton mirroring `roles/vaultwarden`. `tasks.yml` is filled in Task 3; here it is a no-op placeholder so the role is loadable and the guard pattern is in place.

**Files:**
- Create: `roles/coolify/defaults/main.yml`
- Create: `roles/coolify/handlers/main.yml`
- Create: `roles/coolify/tasks/main.yml`
- Create: `roles/coolify/tasks/tasks.yml` (placeholder)

- [ ] **Step 1: Create `roles/coolify/defaults/main.yml`**

```yaml
---
role_coolify_played: false

# Official Coolify installer
coolify_install_url: "https://cdn.coollabs.io/coolify/install.sh"

# Paths created by the Coolify installer
coolify_base_dir: /data/coolify
coolify_source_compose: /data/coolify/source/docker-compose.yml
coolify_proxy_dir: /data/coolify/proxy
coolify_proxy_compose: /data/coolify/proxy/docker-compose.yml

# reverse_proxy_ip comes from group_vars/coolify/vars.yaml
```

- [ ] **Step 2: Create `roles/coolify/handlers/main.yml`**

```yaml
---
- name: Restart coolify proxy
  become: yes
  community.docker.docker_compose_v2:
    project_src: "{{ coolify_proxy_dir }}"
    state: present
    recreate: always
```

- [ ] **Step 3: Create `roles/coolify/tasks/main.yml`**

```yaml
---
- name: Include coolify tasks
  ansible.builtin.include_tasks: tasks.yml
  when: not role_coolify_played
```

- [ ] **Step 4: Create placeholder `roles/coolify/tasks/tasks.yml`**

```yaml
---
- name: Role coolify played
  ansible.builtin.set_fact:
    role_coolify_played: true
```

- [ ] **Step 5: Commit**

```bash
git add roles/coolify/defaults/main.yml roles/coolify/handlers/main.yml roles/coolify/tasks/main.yml roles/coolify/tasks/tasks.yml
git commit -m "feat(coolify): role scaffolding (defaults, handlers, guarded include)"
```

---

## Task 3: Playbook + syntax baseline

Create the play now so the role and group_vars can be syntax-checked end to end before the install logic is added.

**Files:**
- Create: `playbooks/coolify.yaml`

- [ ] **Step 1: Create `playbooks/coolify.yaml`**

Mirrors `playbooks/vaultwarden.yaml` and `playbooks/nextcloud.yaml`.

```yaml
---
- hosts: coolify
  become: yes
  vars:
    install_docker: true
    docker_registry_login: false
    sshd_listen: "{{ ansible_all_ipv4_addresses | ansible.netcommon.ipaddr(admin_network) | first }}"

  roles:
    - common
    - coolify
```

- [ ] **Step 2: Syntax-check the playbook (this loads the role + group_vars)**

Run:
```bash
./venv/bin/ansible-playbook playbooks/coolify.yaml --syntax-check
```
Expected: `playbook: playbooks/coolify.yaml` with no errors. (This will fail loudly if the role files from Task 2 have YAML errors or a missing `tasks.yml`.)

- [ ] **Step 3: Commit**

```bash
git add playbooks/coolify.yaml
git commit -m "feat(coolify): playbook binding coolify group to common + coolify roles"
```

---

## Task 4: Coolify install + Traefik PROXY-protocol patch

Replace the placeholder `tasks.yml` with the real install and Traefik-trust logic.

**Files:**
- Modify: `roles/coolify/tasks/tasks.yml`

**Background for the implementer — Coolify's Traefik command.** The Coolify
installer generates `/data/coolify/proxy/docker-compose.yml`. Its `traefik`
service has a `command:` list of single-quoted CLI flags, including entrypoint
definitions named `http` (`:80`) and `https` (`:443`), e.g.:

```yaml
    command:
      - '--ping=true'
      - '--entrypoints.http.address=:80'
      - '--entrypoints.https.address=:443'
      - '--providers.docker=true'
```

To make Traefik accept the PROXY-protocol header proxy1 sends on `:443` (and
recover the real client IP), two flags must be added to the `https` entrypoint.
`proxyProtocol.trustedIPs` is a **static** entrypoint setting, so it must live in
this command list — it cannot be supplied via Traefik's dynamic file provider.

- [ ] **Step 1: Write the full `roles/coolify/tasks/tasks.yml`**

```yaml
---
# ── Install Coolify (official installer, idempotent) ─────────────

- name: Download Coolify install script
  ansible.builtin.get_url:
    url: "{{ coolify_install_url }}"
    dest: /opt/coolify-install.sh
    mode: "0755"

- name: Run Coolify installer
  ansible.builtin.command: bash /opt/coolify-install.sh
  args:
    creates: "{{ coolify_source_compose }}"
  register: coolify_install
  # The installer is non-interactive when stdin is not a TTY (Ansible).

- name: Wait for Coolify proxy compose to be generated
  ansible.builtin.stat:
    path: "{{ coolify_proxy_compose }}"
  register: coolify_proxy_stat
  until: coolify_proxy_stat.stat.exists
  retries: 30
  delay: 5

# ── Trust proxy1's PROXY protocol on the https (:443) entrypoint ──
# proxy1's shared stream server sends `proxy_protocol on` to every passthrough
# backend, so Coolify's Traefik must accept it on :443. These edits are
# idempotent (the line regexp prevents duplicates) and re-applied on every run
# in case Coolify regenerated the compose from its UI.

- name: Trust PROXY protocol from proxy1 on https entrypoint
  ansible.builtin.lineinfile:
    path: "{{ coolify_proxy_compose }}"
    insertafter: "--entrypoints\\.https\\.address=:443"
    regexp: "--entrypoints\\.https\\.proxyProtocol\\.trustedIPs="
    line: "      - '--entrypoints.https.proxyProtocol.trustedIPs={{ reverse_proxy_ip }}'"
  notify: Restart coolify proxy

- name: Trust forwarded headers from proxy1 on https entrypoint
  ansible.builtin.lineinfile:
    path: "{{ coolify_proxy_compose }}"
    insertafter: "--entrypoints\\.https\\.address=:443"
    regexp: "--entrypoints\\.https\\.forwardedHeaders\\.trustedIPs="
    line: "      - '--entrypoints.https.forwardedHeaders.trustedIPs={{ reverse_proxy_ip }}'"
  notify: Restart coolify proxy

- name: Role coolify played
  ansible.builtin.set_fact:
    role_coolify_played: true
```

- [ ] **Step 2: Syntax-check the playbook again**

Run:
```bash
./venv/bin/ansible-playbook playbooks/coolify.yaml --syntax-check
```
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add roles/coolify/tasks/tasks.yml
git commit -m "feat(coolify): install via official script + trust proxy1 PROXY protocol in Traefik"
```

> **Implementer note for deploy time (not a code step):** after the first real
> run, open `/data/coolify/proxy/docker-compose.yml` on the host and confirm the
> two `--entrypoints.https.*.trustedIPs=192.168.0.225` lines landed *inside* the
> `traefik` `command:` list with matching indentation (6 spaces, as written). If
> Coolify uses different indentation, adjust the `line:` leading spaces in
> `tasks.yml` to match, and confirm the entrypoint is named `https` (grep the
> file for `entrypoints.`). This is the one spot tied to Coolify's generated
> layout.

---

## Task 5: Wire Coolify into proxy1 (nginx)

Additive edits to `inventories/group_vars/common.yaml`. None of these touch
existing entries; exact `server_name` / SNI entries keep precedence over the new
`*.zozoh.fr` wildcard.

**Files:**
- Modify: `inventories/group_vars/common.yaml`

- [ ] **Step 1: Add `coolify` to `group_hosts`**

Find the `group_hosts:` map (currently ends with `lagabelle-saint-hilaire: 192.168.0.129`) and add the `coolify` line:

```yaml
group_hosts:
  gitlab: 192.168.0.66
  proxmox: 192.168.0.254
  mail: 192.168.0.33
  vaultwarden: 192.168.0.161
  cloud: 192.168.0.98
  lagabelle-saint-hilaire: 192.168.0.129
  coolify: 192.168.0.193
```

- [ ] **Step 2: Add the `coolify` upstream**

Find `nginx_upstreams:` and append a new entry after the `mail` upstream:

```yaml
  - name: coolify
    servers:
      - "coolify:80"
```

- [ ] **Step 3: Add Coolify SNI routes to the stream map**

Inside `nginx_extra_conf_options`, in the `map $ssl_preread_server_name $targetBackend { hostnames; … }` block, add the exact dashboard route alongside the other entries and the wildcard catch-all as the **last** entry before the closing `}` of the map:

```nginx
      vaultwarden.zozoh.fr 192.168.0.161:443;

      mail.zozoh.fr 192.168.0.33:443;

      autoconfig.zozoh.fr 192.168.0.33:443;
      autodiscover.zozoh.fr 192.168.0.33:443;
      autoconfig.lagabelle-saint-hilaire.fr 192.168.0.33:443;
      autodiscover.lagabelle-saint-hilaire.fr 192.168.0.33:443;

      coolify.zozoh.fr 192.168.0.193:443;
      *.zozoh.fr 192.168.0.193:443;
```

(The exact `coolify.zozoh.fr` line is redundant with the wildcard but kept
explicit for readability and so production can later move it without touching the
wildcard. `hostnames;` guarantees existing exact names like `gitlab.zozoh.fr` and
`vaultwarden.zozoh.fr` still win over `*.zozoh.fr`; the apex `zozoh.fr` is not
matched by the wildcard.)

- [ ] **Step 4: Add the Coolify port-80 vhost**

Find `nginx_vhosts:` and append a new vhost after the `mail.zozoh.fr` port-80 vhost (the last entry in the list):

```yaml
  - listen: "80"
    server_name: "coolify.zozoh.fr *.zozoh.fr"
    filename: "coolify.zozoh.fr-80.conf"
    extra_parameters: |
      location / {
        gzip                    off;

        {{ _nginx_proxy_headers | indent(8) }}

        proxy_pass http://coolify;
      }
```

- [ ] **Step 5: Verify common.yaml still parses and proxy1 resolves it**

Run:
```bash
./venv/bin/ansible-inventory --host proxy1.ovh.zozoh.fr >/dev/null && echo OK
```
Expected: `OK` (no YAML parse error). The command fails loudly if the edits broke `common.yaml`.

- [ ] **Step 6: Syntax-check the proxy playbook**

Run:
```bash
./venv/bin/ansible-playbook playbooks/proxy.yaml --syntax-check
```
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add inventories/group_vars/common.yaml
git commit -m "feat(coolify): route coolify.zozoh.fr + *.zozoh.fr through proxy1 nginx"
```

---

## Task 6: Pre-deploy dry-run + deploy/verify checklist

No code in this task — it produces the dry-run evidence and records the manual
steps that complete the rollout. Each command is gated on host connectivity.

- [ ] **Step 1: Dry-run the Coolify playbook against the host**

Per the project's risky-ops convention, scope to the single host. The `common`
role will report changes; the `install.sh` step shows as a change on first run
(it is guarded by `creates:` so it is a no-op on subsequent runs).

Run:
```bash
./venv/bin/ansible-playbook playbooks/coolify.yaml --limit coolify.ovh.zozoh.fr --check --diff
```
Expected: connects as `debian@coolify.ovh.zozoh.fr`, no fatal errors. Note that
`--check` cannot fully simulate the shell installer or the docker_compose
handler; treat a clean connection + gathered facts + planned `common` changes as
success for the dry-run.

- [ ] **Step 2: Dry-run the proxy playbook against proxy1**

Run:
```bash
./venv/bin/ansible-playbook playbooks/proxy.yaml --limit proxy1.ovh.zozoh.fr --check --diff
```
Expected: `--diff` shows the new `coolify.zozoh.fr-80.conf` vhost, the added
`coolify` upstream, and the stream-map additions; no changes to existing service
vhosts.

- [ ] **Step 3: Real deploy (operator-run, in order)**

```bash
# 1) Install Coolify on the new host
./venv/bin/ansible-playbook playbooks/coolify.yaml --limit coolify.ovh.zozoh.fr

# 2) Publish routing on proxy1
./venv/bin/ansible-playbook playbooks/proxy.yaml --limit proxy1.ovh.zozoh.fr
```

- [ ] **Step 4: Manual post-install steps (from the spec)**

1. Browse to the dashboard (initially reachable on the host's `:8000` over the
   admin network) and create the initial Coolify admin account.
2. **Settings → Instance domain**: set `https://coolify.zozoh.fr` so Traefik
   routes the dashboard and issues its Let's Encrypt cert (HTTP-01 succeeds via
   proxy1's new `:80` vhost).
3. On the host, confirm the two `--entrypoints.https.*.trustedIPs=192.168.0.225`
   flags are present in `/data/coolify/proxy/docker-compose.yml` and the proxy
   container restarted (see Task 4 implementer note).
4. **Verify risk #2 (sshd bind vs. Coolify localhost server):** in Coolify, the
   built-in "localhost" server must reach this host's Docker over SSH. The
   `common` role binds sshd to the admin-network IP (`192.168.1.193`). If
   Coolify's localhost server is configured with an IP sshd is not listening on,
   it will fail to connect — set Coolify's server IP to `192.168.1.193` (or
   adjust the sshd bind) until the connection check passes.

- [ ] **Step 5: Functional verification**

- `https://coolify.zozoh.fr` loads the dashboard with a valid Let's Encrypt cert.
- Deploy a throwaway app under some `*.zozoh.fr` name; confirm it serves through
  proxy1 with a valid cert.
- Confirm existing services (gitlab, vaultwarden, cloud, mail) still load — exact
  SNI/vhost precedence held.
- Confirm Coolify/Traefik access logs show the real client IP, not
  `192.168.0.225` (PROXY protocol working).

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Install via official script → Task 4. ✔
- TLS passthrough + Traefik terminates → Tasks 4 (Traefik trust) + 5 (stream passthrough). ✔
- Coolify host IP/networks → Task 1. ✔
- `coolify.zozoh.fr` dashboard + `*.zozoh.fr` wildcard → Task 5 (stream map + :80 vhost). ✔
- Production app domains added manually later → documented (explicit `coolify.zozoh.fr` line kept; wildcard precedence noted). ✔
- Traefik PROXY-protocol trust automated → Task 4, idempotent re-apply + manual fallback noted. ✔
- Risk: Traefik static-config drift → Task 4 implementer note + idempotent lineinfile. ✔
- Risk: sshd bind vs Coolify localhost server → Task 6 Step 4.4. ✔
- Risk: wildcard blast radius → Task 5 Step 3 note. ✔
- Manual post-install (admin account, instance FQDN) → Task 6 Step 4. ✔

**Placeholder scan:** none — every code/config step shows full content; the only
deploy-time discretion (Traefik command indentation/entrypoint name) is an
explicit, bounded verification note, not a TODO.

**Type/name consistency:** `role_coolify_played`, `coolify_proxy_dir`,
`coolify_proxy_compose`, `coolify_source_compose`, `coolify_install_url`,
`reverse_proxy_ip`, upstream name `coolify`, group `coolify`, host
`coolify.ovh.zozoh.fr` — used consistently across defaults, handlers, tasks, and
playbook.
