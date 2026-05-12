# Zozoh Infrastructure - Copilot Instructions

## Overview

This is an Ansible project managing a multi-tier infrastructure hosted on Proxmox VE at OVH. It serves two domains: **zozoh.fr** (personal services) and **cathosphere.co** (web application).
Proxmox it self is managed manually in SSH or via the web UI (but you can use pve firewall cli on the host).

## Network Architecture

### Proxmox Host (Hypervisor)
- **Public IP**: 37.187.24.18
- **SSH**: `ssh enzo@ovh.zozoh.fr -p 222`
- **Role**: Firewall, NAT gateway, Proxmox VE hypervisor
- **Bridge interfaces**: vmbr0 (public), vmbr1 (private VLANs), vmbr2 (admin VLANs)

### VLAN Layout (vmbr1.X)

| VLAN | Subnet | Gateway | Purpose |
|------|--------|---------|---------|
| vmbr1.2 | 192.168.0.32/27 | .62 | Cathosphere |
| vmbr1.3 | 192.168.0.64/27 | .94 | GitLab |
| vmbr1.4 | 192.168.0.96/27 | .126 | Legacy (zozoh.fr services) |
| vmbr1.5 | 192.168.0.128/27 | .158 | Lagabelle |
| vmbr1.4094 | 192.168.0.224/27 | .254 | Common (reverse proxy) |

### Admin VLANs (vmbr2.X) - SSH access only

| VLAN | Subnet | Gateway |
|------|--------|---------|
| vmbr2.2 | 192.168.1.32/27 | .62 |
| vmbr2.3 | 192.168.1.64/27 | .94 |
| vmbr2.4 | 192.168.1.96/27 | .126 |
| vmbr2.5 | 192.168.1.128/27 | .158 |
| vmbr2.4094 | 192.168.1.224/27 | .254 |

## Servers

### Reverse Proxy (proxy1.ovh.zozoh.fr)
- **Private IP**: 192.168.0.225 (vmbr1.4094)
- **SSH**: `ssh debian@proxy1.ovh.zozoh.fr` (via bastion)
- **Services**: Nginx reverse proxy with SSL termination (Let's Encrypt)
- **HTTPS handling**: TCP stream passthrough (SNI-based routing) on port 443, HTTP proxy on port 80
- **Proxied backends**:
  - `.zozoh.fr` → 192.168.0.97:443 (legacy, with proxy_protocol)
  - `gitlab.zozoh.fr`, `registry.zozoh.fr`, `pages.zozoh.fr` → localhost:4443 (GitLab)
  - `lagabelle-saint-hilaire.fr` → 192.168.0.129:8000

### Legacy Server (zozoh.fr services)
- **Private IP**: 192.168.0.97 (vmbr1.4)
- **SSH**: `ssh enzo@legacy.ovh.zozoh.fr -p 2222` (direct) or via port forward on bastion (dport 22222)
- **OS**: Debian
- **Services**:
  - **Nextcloud 28** at `cloud.zozoh.fr` — PHP 8.1-FPM, PostgreSQL 13, Redis, APCu
  - **Collabora Online** (coolwsd) at `collaboraonline.zozoh.fr` — port 9980, SSL disabled (behind nginx)
  - **Mail server** (Postfix + Dovecot) for `zozoh.fr` — ports 25, 465, 587, 993, 143, 110, 995
  - **Rainloop webmail** at `mail.zozoh.fr`
  - **PostfixAdmin** at `postfix.zozoh.fr`
  - **Nginx** handles all vhosts with proxy_protocol on port 443
- **Important config notes**:
  - `/etc/hosts` has `cloud.zozoh.fr → 192.168.0.98` (for WOPI callbacks via reverse proxy)
  - cloud-init `manage_etc_hosts` is disabled
  - Nextcloud `wopi_url` = `http://127.0.0.1:9980` (server-to-Collabora)
  - Nextcloud `public_wopi_url` = `https://collaboraonline.zozoh.fr` (browser-to-Collabora)
  - Nextcloud `allow_local_remote_servers` = true
  - coolwsd `capabilities` = false (uses coolforkit-ns, not coolforkit-caps)

### Reverse Proxy (192.168.0.98)
- **Same machine as proxy1** but accessed via vmbr1.4 IP
- **Role**: Routes zozoh.fr traffic to legacy server with proxy_protocol

### GitLab Server (gitlab.ovh.zozoh.fr)
- **Private IP**: 192.168.0.66 (vmbr1.3)
- **SSH**: `ssh debian@gitlab.ovh.zozoh.fr` (via bastion)
- **Services**: GitLab CE, Docker Registry (port 5678), GitLab Pages
- **URLs**: `gitlab.zozoh.fr`, `registry.zozoh.fr`, `pages.zozoh.fr`

### GitLab Runner (gitlab-runner1.ovh.zozoh.fr)
- **Private IP**: 192.168.0.67 (vmbr1.3)
- **Executor**: Shell

### Cathosphere Stack (192.168.0.32/27)

| Server | IP | Services |
|--------|----|----------|
| pg1.cs.ovh.zozoh.fr | .34 | PostgreSQL 15 + PostGIS + Redis |
| es1.cs.ovh.zozoh.fr | .33 | Elasticsearch (6-8GB heap) |
| broker1.cs.ovh.zozoh.fr | .35 | RabbitMQ 3.11.10 + Redis |
| web1.cs.ovh.zozoh.fr | .36 | Docker: API(:9981), Sign(:9982), WS(:9983), Admin(:9984) |
| batch1.cs.ovh.zozoh.fr | .39 | Docker: Background batch processor |
| proxy1.cs.ovh.zozoh.fr | .37 | Nginx proxy for Cathosphere |
| media.cs.ovh.zozoh.fr | .38 | MinIO S3 storage (:9000 API, :9001 console) |

### Lagabelle (192.168.0.128/27)
- **Server**: 192.168.0.129
- **Services**: Docker-based web app (:8000) + media (:8001)
- **Database**: PostgreSQL + Redis (on legacy server via Docker)

## Connection Patterns

- **Bastion host**: `enzo@ovh.zozoh.fr:222` (Proxmox host)
- **All internal hosts**: SSH via ProxyJump through bastion
- **Ansible user**: `debian` (with sudo)
- **Legacy server user**: `enzo` (sudo password required)
- **NAT**: Proxmox handles DNAT from public IP to internal VMs
- **Proxy protocol**: Reverse proxy adds proxy_protocol header for HTTPS traffic to legacy server

## Ansible Project Structure

```
ansible.cfg          # inventory=inventories/, remote_user=debian
ssh_config           # ProxyJump config for *.ovh.zozoh.fr
requirements.yml     # External roles (geerlingguy.*, riemers.gitlab-runner)
inventories/
  all.yaml           # Host inventory
  group_vars/
    all.yaml          # Global vars (domain, admin_email, common_tools)
    common.yaml       # Common proxy group vars
    gitlab.yaml       # GitLab + runner config
    cs/
      vars.yaml       # Cathosphere app config (ports, IPs, API keys)
      vault.yaml      # Encrypted secrets
playbooks/
  proxy.yaml          # Reverse proxy setup
  gitlab.yaml         # GitLab server
  gitlab_runner.yaml  # GitLab Runner
  pg.yaml             # PostgreSQL
  broker.yaml         # RabbitMQ + Redis
  web.yaml            # Cathosphere web (Docker)
  batch.yaml          # Cathosphere batch (Docker)
  media.yaml          # MinIO
  mail.yaml           # Mail server (not in active inventory)
roles/
  common/             # Base config: /etc/hosts, SSH, unattended-upgrades, Docker
  cs/                 # Cathosphere Docker deployment
  mailserver/         # Full mail stack (Postfix, Dovecot, DKIM, SpamAssassin, fail2ban)
  minio/              # MinIO S3 storage
  rabbitmq/           # RabbitMQ broker
```

## Important Notes

- All secrets are stored in **Ansible Vault** (`vault.yaml` files)
- The legacy server (zozoh.fr) is **NOT managed by Ansible** — it's manually configured
- Docker images are pulled from `registry.zozoh.fr` (private GitLab registry)
- PostgreSQL uses **md5 auth** on private network, **peer auth** locally
- Elasticsearch has **security disabled** (single-node, internal only)
