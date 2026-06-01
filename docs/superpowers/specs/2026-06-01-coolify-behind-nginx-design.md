# Coolify behind nginx (proxy1) — Design

**Date:** 2026-06-01
**Status:** Approved (design), pending spec review

## Goal

Provision a new `coolify` host that runs [Coolify](https://coolify.io) (self-hosted
PaaS), with the Coolify dashboard reachable at `https://coolify.zozoh.fr` and all
sites served by Coolify — including the dashboard — fronted by the existing public
nginx reverse proxy (`proxy1.ovh.zozoh.fr`).

## Decisions

| Topic | Decision |
| --- | --- |
| Install method | Official `install.sh`, run via Ansible, guarded for idempotency |
| TLS model | **Passthrough** — proxy1 `ssl_preread`-routes raw TLS to `coolify:443`; Coolify's internal **Traefik** terminates TLS and auto-issues Let's Encrypt certs (dashboard + per app) via HTTP-01 |
| Coolify host | `coolify.ovh.zozoh.fr`, proxmox vmid 208, private `192.168.0.193`, admin `192.168.1.193`, /27 block `192.168.0.192/27` |
| Reverse proxy IP | `192.168.0.225` (proxy1) |
| Domains now | `coolify.zozoh.fr` (dashboard) + wildcard `*.zozoh.fr` catch-all on proxy1 for homelab test apps |
| Domains later | Production app domains added manually per-app (explicit stream map + vhost), as needed |
| Traefik PROXY-protocol trust | **Automated** by the `coolify` role; manual UI step documented as fallback |

## Architecture / traffic flow

proxy1 is the only public-facing host. It keeps its current nginx config and gains
additive entries for Coolify.

```
HTTPS  client ──TLS(SNI)──▶ proxy1:443  nginx stream (ssl_preread, proxy_protocol on)
                              │  exact   "coolify.zozoh.fr"  ┐
                              │  wildcard "*.zozoh.fr"        ├─▶ 192.168.0.193:443
                              ▼                              ┘     Coolify Traefik
                                                                   (terminates TLS,
                                                                    trusts PROXY from
                                                                    192.168.0.225)

HTTP   client ──▶ proxy1:80  nginx vhost (server_name "coolify.zozoh.fr *.zozoh.fr")
                              └─ proxy_pass http://coolify  ─▶ 192.168.0.193:80
                                 (Traefik web entrypoint: ACME HTTP-01 + 301→https)
```

Per-host HTTP-01 issuance works because, for any name matching `coolify.zozoh.fr` or
`*.zozoh.fr`, both `:443` (passthrough) and `:80` (http) reach Coolify. No DNS-01 or
wildcard certificate is required.

**nginx matching precedence (verified against current `common.yaml`):**

- Stream `map $ssl_preread_server_name` uses `hostnames;`, so exact entries
  (`gitlab.zozoh.fr`, `vaultwarden.zozoh.fr`, …) take precedence over the
  `*.zozoh.fr` wildcard. The wildcard only catches otherwise-unmatched
  `*.zozoh.fr` SNIs.
- Wildcard `*.zozoh.fr` does **not** match the apex `zozoh.fr` (mailserver),
  and does not match `*.lagabelle-saint-hilaire.fr`. Both are unaffected.
- The same precedence holds for the port-80 `server_name` wildcard vhost: existing
  exact `*-80.conf` vhosts win; the wildcard is the fallback.

## Components

### 1. `inventories/group_vars/coolify/vars.yaml` (new)

```yaml
private_network: "192.168.0.192/27"
admin_network: "192.168.1.192/27"

group_hosts:
  coolify.ovh.zozoh.fr: 192.168.0.193

coolify_domain: coolify.zozoh.fr

# proxy1 — trusted source of the PROXY protocol header on :443
reverse_proxy_ip: 192.168.0.225
```

### 2. `playbooks/coolify.yaml` (new)

Mirrors `playbooks/vaultwarden.yaml`:

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

`install_docker: true` keeps Docker provisioning consistent with the other hosts;
`install.sh` detects the existing Docker and reuses it.

### 3. `roles/coolify/` (new)

Shape mirrors `roles/vaultwarden/` (idempotent include guard via `role_coolify_played`).

- **`defaults/main.yml`**
  - `coolify_install_url: https://cdn.coollabs.io/coolify/install.sh`
  - `coolify_domain` (from group_vars)
  - `reverse_proxy_ip` (from group_vars)
  - `coolify_proxy_compose: /data/coolify/proxy/docker-compose.yml`
- **`tasks/tasks.yml`**
  1. Ensure `/data` exists.
  2. Download and run `install.sh` non-interactively, guarded by
     `creates: /data/coolify/source/docker-compose.yml`.
  3. Inject the Traefik PROXY-protocol trust flag (see below), notify proxy restart.
- **`tasks/main.yml`** — `include_tasks: tasks.yml when: not role_coolify_played`.
- **`handlers/main.yml`** — restart the Coolify proxy
  (`docker compose -f {{ coolify_proxy_compose }} up -d --force-recreate proxy`,
  or `coolify` CLI equivalent if present).

**Traefik PROXY-protocol automation.** `proxyProtocol.trustedIPs` is a Traefik
*static* entrypoint setting that Coolify writes into the generated proxy compose at
`/data/coolify/proxy/docker-compose.yml` as a `--entrypoints.https.*` command flag.
The role appends:

```
--entrypoints.https.proxyProtocol.trustedIPs={{ reverse_proxy_ip }}
--entrypoints.https.forwardedHeaders.trustedIPs={{ reverse_proxy_ip }}
```

to the Traefik `command:` list (idempotently — skip if already present), then
restarts the proxy via the handler. This makes Traefik accept the PROXY header
proxy1 sends and recover the real client IP.

> **Brittleness note:** Coolify can regenerate `proxy/docker-compose.yml` when the
> proxy is changed from its UI, dropping the flag. If that happens, re-run the
> playbook, or set it once manually in Coolify (**Server → Proxy → Configuration**).
> The task must be written to re-apply cleanly on every run.

### 4. `inventories/group_vars/common.yaml` (edit — additive, proxy1)

- Add to `group_hosts`:
  ```yaml
  coolify: 192.168.0.193
  ```
- Add upstream:
  ```yaml
  - name: coolify
    servers:
      - "coolify:80"
  ```
- Add to the `stream { map $ssl_preread_server_name $targetBackend { … } }` block:
  ```nginx
  coolify.zozoh.fr 192.168.0.193:443;
  *.zozoh.fr       192.168.0.193:443;
  ```
- Add a port-80 vhost (ACME + http→https handled by Traefik upstream):
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

No certbot entry is needed on proxy1 for Coolify (Coolify's Traefik owns the certs).

## Inventory

`inventories/all.yaml` already declares the host:

```yaml
coolify:
  hosts:
    coolify.ovh.zozoh.fr:
      proxmox_vmid: 208
```

No inventory change required.

## Error handling / idempotency

- `install.sh` step is guarded by `creates:`, so re-runs are no-ops once installed.
- Traefik flag injection is written to be idempotent (presence-checked before edit).
- proxy1 changes are declarative nginx config rendered by `geerlingguy.nginx`;
  re-running `playbooks/proxy.yaml` reconciles them.

## Risks & open items

1. **Traefik static-config drift (primary risk).** See brittleness note above.
   Mitigation: idempotent re-apply + documented manual fallback.
2. **`sshd_listen` vs. Coolify's localhost server.** The `common` role binds sshd to
   the admin-network IP only. Coolify manages its own host's Docker over SSH
   ("localhost" server). If Coolify dials an IP sshd is not listening on, it cannot
   reach its own Docker. **Verify** after install that Coolify's localhost server
   target matches the sshd bind address; adjust Coolify's server IP or the sshd
   listen address if not.
3. **Wildcard blast radius.** `*.zozoh.fr` routes every otherwise-unmatched
   `*.zozoh.fr` name to Coolify. Acceptable for homelab; documented so future
   production names are added as explicit entries *before* relying on the wildcard.

## Manual post-install steps (not cleanly scriptable)

1. Create the initial Coolify admin account (first-run web setup at the dashboard).
2. Set the instance FQDN to `https://coolify.zozoh.fr` (**Settings → Instance domain**)
   so Traefik routes the dashboard and issues its cert.
3. Verify risk #2 (Coolify localhost server reachability).

## Verification

- `https://coolify.zozoh.fr` loads the dashboard with a valid LE cert.
- Deploy a throwaway app under `*.zozoh.fr`; confirm it gets a valid cert and serves
  through proxy1.
- Confirm existing services (gitlab, vaultwarden, cloud, mail) are unaffected after
  re-running `playbooks/proxy.yaml` (exact SNI/vhost precedence holds).
- Confirm real client IP appears in Coolify/Traefik logs (PROXY protocol working),
  not `192.168.0.225`.
