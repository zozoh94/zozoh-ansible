# Middle Monitor server and agent fleet - Design

**Date:** 2026-10-07
**Status:** Draft (pending spec review)

## Goal

Stand up a self-hosted [Middle Monitor](https://github.com/middle-monitor/middle-monitor)
instance (a self-hostable "Sentry-like" platform: uptime checks, host metrics,
errors, traces, logs and alerting) on a new Proxmox VM, reachable at
`https://monitor.zozoh.fr`, and install the Middle Monitor host agent on every
other managed machine so each reports its host metrics to that instance.

Two reusable Ansible roles come out of this:

1. `middlemonitor` : configures the server VM (Docker Compose stack + nginx/TLS front).
2. `middlemonitor-agent` : installs and configures the host agent on a machine.

## Caveat (read first)

Middle Monitor is a brand-new project (repository created 2026-10-05, single-digit
stars at design time, no tagged release yet). Its self-hosting and agent docs are
well written and explicitly Ansible-aware, but this is young software that will
supervise the whole fleet. Pinning (`middlemonitor_version`, a git ref for the
compose files) is built into the design so upgrades are deliberate. Accepting that
risk is the operator's call; this design assumes it is accepted.

## Decisions

| Topic | Decision |
| --- | --- |
| VM creation | Manual, by the operator (matches current workflow: no VM provisioning automation exists in the repo). Ansible configures the VM only. |
| Server host | `monitor.ovh.zozoh.fr`, proxmox vmid `211`, Debian 12, 4 vCPU / 6 GB RAM / 40 GB disk |
| Networking | Two new `/24` planes opened (existing `/24` is fully carved). Service `192.168.3.0/24`, admin `192.168.4.0/24`. Monitor takes a `/27` in each, following the house gateway rule (gateway = network + 30, on the Proxmox host). |
| Monitor service IP | `192.168.3.1` in block `192.168.3.0/27`, gateway `192.168.3.30` |
| Monitor admin IP | `192.168.4.1` in block `192.168.4.0/27`, gateway `192.168.4.30` |
| Public domain | `monitor.zozoh.fr` |
| TLS model | Self-terminated on the VM (nginx + certbot, mirrors `vaultwarden`). proxy1 does SNI passthrough with PROXY protocol, exactly like vaultwarden. The upstream Caddy stays as the internal path router behind nginx. |
| Deploy artifact | Fetch the upstream `deploy/self-hosted` compose and Caddyfile at a pinned ref unchanged; template only `.env`. Caddy is bound to localhost by setting `HTTP_PORT=127.0.0.1:8000` (the upstream line is `"${HTTP_PORT:-8000}:80"`). Images pulled from `ghcr.io/middle-monitor` pinned by `MM_VERSION`. |
| Agent install | Fleet path from the agent docs: `get_url` of the versioned binary with checksum from the API's own `/sha256` endpoint, templated `config.yaml` validated with `--config-check`, systemd unit with `ExecReload=/bin/kill -HUP $MAINPID`. No `curl \| bash`. |
| Agent scope | All OVH managed VMs (inventory `all` minus `monitor`) plus the odroid home box (separate inventory, reaches the API over the public URL). |
| Agent API URL | `https://monitor.zozoh.fr` everywhere. OVH hosts skip the hairpin via a `global_hosts` entry (`monitor.zozoh.fr` -> `192.168.3.1`), the same trick already used for `smtp.zozoh.fr`. odroid uses public DNS. |
| Agent auth | An org install token generated from the Middle Monitor UI **after** the server is up, stored in the vault. This creates a hard ordering dependency (see Deployment flow). |

## Architecture / traffic flow

proxy1 stays the only public-facing host and gains additive entries for monitor,
mirroring the vaultwarden entries.

```
Dashboard (browser / odroid agent over public):
  client --TLS(SNI monitor.zozoh.fr)--> proxy1:443  nginx stream (ssl_preread, proxy_protocol on)
                                          --> 192.168.3.1:443  monitor nginx (terminates TLS, LE cert)
                                              --> 127.0.0.1:8000  Caddy (path router)
                                                  --> api / receiver / frontend

OVH agent (no hairpin):
  agent --> https://monitor.zozoh.fr  (/etc/hosts: monitor.zozoh.fr = 192.168.3.1)
         --> 192.168.3.1:443  monitor nginx --> Caddy --> receiver

ACME HTTP-01 for the monitor cert:
  client --> proxy1:80  nginx vhost (server_name monitor.zozoh.fr)
         --> proxy_pass http://monitor --> 192.168.3.1:80  monitor nginx (/var/www/certbot)
```

Inside the VM, the Middle Monitor stack is the upstream Docker Compose: Caddy,
`api`, `receiver`, `worker`, `frontend`, PostgreSQL, OpenSearch and Kafka. nginx
sits in front of Caddy only to terminate TLS and accept the PROXY protocol from
proxy1, reusing the certbot flow the fleet already relies on.

## Components

### 1. `inventories/all.yaml` (edit, additive)

Add the monitor group and host:

```yaml
monitor:
  hosts:
    monitor.ovh.zozoh.fr:
      proxmox_vmid: 211
```

The odroid host is added for the agent rollout (see component 7).

### 2. `inventories/group_vars/monitor/vars.yaml` (new)

```yaml
private_network: "192.168.3.0/27"
admin_network: "192.168.4.0/27"

group_hosts:
  monitor.ovh.zozoh.fr: 192.168.3.1

# Public domain, fronted by proxy1 (TLS terminated on this VM)
middlemonitor_domain: monitor.zozoh.fr

# proxy1, the only host trusted to send the PROXY protocol header on :443
reverse_proxy_ip: 192.168.0.225

# Pinning (see Caveat)
middlemonitor_ref: main       # git ref for the compose/Caddyfile fetched from the repo
middlemonitor_version: latest # MM_VERSION, the ghcr image tag
```

### 3. `inventories/group_vars/monitor/vault.yaml` (new, encrypted)

Server secrets, generated by the operator and encrypted with the repo's
`encrypt-vault-values.py`:

- `middlemonitor_jwt_secret` (`openssl rand -base64 32`)
- `middlemonitor_db_password`
- `middlemonitor_credentials_encryption_key` (`openssl rand -base64 32`)
- `middlemonitor_unsub_secret` (`openssl rand -hex 32`)
- `middlemonitor_seed_admin_email`, `middlemonitor_seed_admin_password`
- `middlemonitor_smtp_password` (optional, if wiring alert mail through `smtp.zozoh.fr`)

### 4. `roles/middlemonitor/` (new, server role)

Shape mirrors `roles/vaultwarden/` (idempotent include guard via
`role_middlemonitor_played`, `defaults/`, `handlers/`, `templates/`, `tasks/`).

- **`defaults/main.yml`**
  - `role_middlemonitor_played: false`
  - `middlemonitor_dir: /opt/middlemonitor` (holds the compose files and `.env`)
  - `middlemonitor_bind: "127.0.0.1:8000"` (HTTP_PORT value; Caddy bound to localhost)
  - `middlemonitor_upstream: "127.0.0.1:8000"` (what the VM nginx proxies to)
  - `middlemonitor_public_url: "https://{{ middlemonitor_domain }}"`

- **`tasks/tasks.yml`**
  1. Create `{{ middlemonitor_dir }}`.
  2. `get_url` the upstream `deploy/self-hosted/docker-compose.yml` and `Caddyfile`
     at `{{ middlemonitor_ref }}` into `{{ middlemonitor_dir }}/`, unchanged. (The
     compose file carries `build:` keys that stay dormant under
     `pull_policy: missing`; images are pulled from ghcr. Build context is resolved
     only at build time, which never runs here.)
  3. Template `{{ middlemonitor_dir }}/.env` (mode 0640, `no_log`) with:
     `MM_VERSION={{ middlemonitor_version }}`, `PUBLIC_URL={{ middlemonitor_public_url }}`,
     `HTTP_PORT={{ middlemonitor_bind }}` (binds Caddy to localhost), `JWT_SECRET`,
     `DB_PASSWORD`, `CREDENTIALS_ENCRYPTION_KEY`, `UNSUB_SECRET`, `SEED_ADMIN_*`,
     `EXPLAIN_MODE=rules`, and SMTP vars when configured.
  4. nginx + certbot front, copied from the vaultwarden role verbatim in shape:
     install `nginx`, `certbot`, `python3-certbot-nginx`, `openssl`; create
     `/var/www/certbot`; self-signed cert fallback; detect an existing LE cert and
     prefer it; template `middlemonitor-nginx.conf.j2` (listen 80 for ACME + 301,
     listen 443 ssl proxy_protocol, `set_real_ip_from {{ reverse_proxy_ip }}`,
     `proxy_pass http://{{ middlemonitor_upstream }}`); certbot renew cron.
  5. `community.docker.docker_compose_v2` up from `{{ middlemonitor_dir }}`.
  6. Set `role_middlemonitor_played: true`.

- **`handlers/main.yml`** : Reload/Restart nginx; Restart docker compose
  (`docker_compose_v2` with `project_src: {{ middlemonitor_dir }}`).

- **`templates/`** : `.env.j2`, `middlemonitor-nginx.conf.j2`.

### 5. `playbooks/middlemonitor.yaml` (new)

Mirrors `playbooks/vaultwarden.yaml`:

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

### 6. `roles/middlemonitor-agent/` (new, agent role)

Self-contained (does not depend on the `common` role), so it runs the same on OVH
VMs and on the odroid box.

- **`defaults/main.yml`**
  - `role_middlemonitor_agent_played: false`
  - `middlemonitor_agent_api_url: "https://monitor.zozoh.fr"`
  - `middlemonitor_agent_config_dir: /etc/middle-monitor`
  - `middlemonitor_agent_bin_dir: /usr/local/bin`
  - metric toggles (`cpu/ram/disk/network: true`), `middlemonitor_agent_interval: 60`

- **`vars/main.yml`** : architecture map
  `{ x86_64: amd64, aarch64: arm64 }` applied to `ansible_architecture`;
  os = `ansible_system | lower` (`linux`).

- **`tasks/tasks.yml`**
  1. Create `{{ middlemonitor_agent_config_dir }}` and its `scrape.d/` include dir.
  2. `get_url` the binary from
     `{{ api_url }}/api/v1/agents/download/{{ os }}/{{ arch }}` to
     `{{ bin_dir }}/middle-monitor-agent-{{ os }}-{{ arch }}`, with
     `checksum: "sha256:{{ api_url }}/api/v1/agents/download/{{ os }}/{{ arch }}/sha256"`
     (the agent docs state this endpoint is in shasum format usable as `get_url`
     `checksum:`; this also gives idempotent re-download and the ETag/304 benefit).
  3. Symlink `{{ bin_dir }}/middle-monitor-agent` to the arch binary (matches the
     install script's layout and the troubleshooting guide).
  4. Template `config.yaml` (mode 0640, `no_log`) into the config dir with
     `api.url`, `api.api_key` (the install token from vault), `host.name`
     (`inventory_hostname`), metric toggles, interval, and
     `scrape.include: {{ config_dir }}/scrape.d/*.yaml`. Use
     `validate: "{{ bin_dir }}/middle-monitor-agent --config-check --config %s"`
     so a broken config never gets installed.
  5. Template the systemd unit `middle-monitor-agent.service` with
     `ExecStart=.../middle-monitor-agent --config .../config.yaml`,
     `ExecReload=/bin/kill -HUP $MAINPID`, `Restart=on-failure`,
     `After=network-online.target`.
  6. `systemd`: daemon_reload, enable, start. Notify a reload handler (SIGHUP) on
     config change and a restart handler on unit/binary change.
  7. Set `role_middlemonitor_agent_played: true`.

- **`handlers/main.yml`** : `Reload middle-monitor-agent` (`state: reloaded`),
  `Restart middle-monitor-agent` (`state: restarted`), plus `daemon_reload`.

- **`templates/`** : `config.yaml.j2`, `middle-monitor-agent.service.j2`.

### 7. Agent rollout plumbing

- **`inventories/group_vars/all/vault.yaml` (edit)** : add
  `middlemonitor_agent_api_key` (the org install token). Shared, since the agent
  goes everywhere.
- **`inventories/group_vars/all/vars.yaml` (edit)** : add
  `monitor.zozoh.fr: 192.168.3.1` to `global_hosts`, so the `common` role points
  OVH hosts at the private IP and avoids the proxy hairpin.
- **odroid** : add an `odroid` group to `inventories/all.yaml` with its connection
  vars (`ansible_host: 83.205.35.25`, `ansible_port: 2222`, `ansible_user: enzo`),
  kept out of fleet-wide plays. The agent role installs the `linux/arm64` binary
  there and uses the public `api.url` with no `global_hosts` override.
- **`playbooks/middlemonitor-agent.yaml` (new)** : two plays, one
  `hosts: all:!monitor:!odroid` (OVH VMs) and one `hosts: odroid`, both applying
  only the `middlemonitor-agent` role. OVH hosts rely on the `global_hosts` entry
  already laid down by `common` during their own service playbooks.

### 8. `inventories/group_vars/common.yaml` (edit, additive, proxy1)

Mirror the vaultwarden entries so proxy1 fronts `monitor.zozoh.fr`:

- `group_hosts`: add `monitor: 192.168.3.1`
- `nginx_upstreams`: add `monitor -> monitor:80`
- stream `map $ssl_preread_server_name`: add
  `monitor.zozoh.fr 192.168.3.1:443;` (an explicit entry wins over the existing
  `*.zozoh.fr` wildcard, verified against the coolify precedence note)
- `nginx_vhosts`: add a `:80` vhost `server_name monitor.zozoh.fr` that
  `proxy_pass http://monitor` (ACME HTTP-01 + redirect handled on the VM)

## Deployment flow (ordering matters)

1. Operator creates the VM on Proxmox (vmid 211, the two NICs on the new service
   and admin `/27`s), installs Debian 12, seeds the `debian` user and SSH.
2. Operator generates the server secrets, encrypts them into
   `group_vars/monitor/vault.yaml`.
3. Run `playbooks/middlemonitor.yaml` : stack comes up, dashboard answers on the VM.
4. Run `playbooks/proxy.yaml` : proxy1 fronts `monitor.zozoh.fr`; certbot on the VM
   issues the LE cert over HTTP-01.
5. Operator logs into `https://monitor.zozoh.fr` with the seeded admin, generates
   the org **install token**, encrypts it into `group_vars/all/vault.yaml`.
6. Run `playbooks/middlemonitor-agent.yaml` : agents install fleet-wide and register.

Steps 5 and 6 cannot precede a running server; the token does not exist until then.

## Error handling / idempotency

- Server: `.env`/compose templates are declarative; `docker_compose_v2` reconciles.
  `get_url` of compose files at a fixed ref is stable. nginx/certbot mirror the
  proven vaultwarden role.
- Agent: `get_url` with the `/sha256` checksum re-downloads only on a real change;
  `--config-check` via `validate:` blocks a broken config; SIGHUP reload avoids
  needless restarts.
- proxy1: declarative `geerlingguy.nginx` config, reconciled by re-running
  `playbooks/proxy.yaml`.

## Risks and open items

1. **Young software (primary).** See Caveat. Mitigation: explicit pinning,
   deliberate upgrades.
2. **Compose fetched from a moving ref.** No tagged release exists yet, so
   `middlemonitor_ref` defaults to `main`. Switch it to a tag once one ships, to
   make the server fully reproducible.
3. **Token ordering.** The agent play fails cleanly (no token) until step 5 is done.
   An assertion on `middlemonitor_agent_api_key` should give a clear message.
4. **odroid in inventory.** Adding the home box to `all.yaml` must not pull it into
   fleet-wide plays. It is isolated in its own group and excluded via
   `all:!monitor:!odroid`; any future `hosts: all` playbook must account for it.
5. **RAM headroom.** OpenSearch alone wants about 3 GB; 6 GB covers the full stack
   with margin. Watch the VM after first boot.

## Success criteria

- `https://monitor.zozoh.fr` loads the dashboard with a valid LE certificate
  through proxy1, and the seeded admin can sign in.
- Existing services (gitlab, vaultwarden, cloud, mail, coolify) are unaffected
  after re-running `playbooks/proxy.yaml`.
- Every OVH VM and the odroid box appear as hosts in the dashboard and report
  CPU/memory/disk/network.
- `playbooks/middlemonitor-agent.yaml` is idempotent: a second run reports no
  change (no binary re-download, no restart).
- `middle-monitor-agent --config-check` passes on every target.
