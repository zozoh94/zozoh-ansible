# WireGuard VPN server — design

**Date:** 2026-07-07
**Branch:** `wireguard`
**Host:** `vpn.ovh.zozoh.fr` (Proxmox VMID 210, group `wireguard`)

## Goal

Stand up the `vpn` VM as a WireGuard server that gives road-warrior clients
access to the fleet's **admin plane** (`192.168.1.0/24`) — a split tunnel.
Peers are declared in the inventory (IaC). VMs must also be able to initiate
connections back to clients.

## Existing infrastructure (do not re-create)

The `vpn` VM (210) is already provisioned and wired differently from the rest
of the fleet — it lives on **VLAN tag 100**, not tag 7, and on the `10.x`
address space instead of `192.168.x`:

| NIC  | Proxmox bridge | VM address       | Gateway     | Role            |
|------|----------------|------------------|-------------|-----------------|
| net0 | `vmbr1` tag100 | `10.0.0.2/24`    | `10.0.0.1`  | internet-facing (NAT out) |
| net1 | `vmbr2` tag100 | `10.100.0.2/24`  | `10.100.0.1`| admin plane     |

On the Proxmox host (`/etc/network/interfaces`, VLAN 100 subinterfaces):

- `vmbr1.100` = `10.0.0.1/24`, `MASQUERADE 10.0.0.0/24 → vmbr0` (public egress).
- `vmbr2.100` = `10.100.0.1/24`, with a **static route already in place**:
  `ip route add 10.200.0.0/24 via 10.100.0.2`.

That route fixes the WireGuard client subnet: **`10.200.0.0/24`**, reachable
from the whole fleet via the VPN VM's admin NIC (`10.100.0.2`). Proxmox is
directly connected to every `192.168.1.x/27` admin subnet, so return traffic to
clients needs no extra config on the individual admin VMs.

Ansible reaches the VM as `debian@vpn.ovh.zozoh.fr` via the `ssh_config`
`ProxyJump enzo@ovh.zozoh.fr:222`, which resolves to the admin IP `10.100.0.2`.

### Prerequisite outside this playbook (manual, Proxmox host)

Inbound WireGuard UDP must be DNAT'd from the public IP to the VM's
internet-facing NIC. There is **no such rule yet**, and the VM is currently not
even reachable on `10.0.0.2`. On the Proxmox host, alongside the other
`vmbr1.100` rules:

```
iptables -t nat -A PREROUTING -i vmbr0 -p udp --dport 51820 -j DNAT --to 10.0.0.2:51820
```

This is managed the same way as the mail/coolify DNAT rules on that host and is
**not** part of this Ansible playbook (Proxmox host is the jump host, not a
managed inventory node).

## Routing model

Split tunnel to the admin plane, **plus masquerade on the tunnel** so either
direction works:

- Server `wg0`: `10.200.0.1/24`. Clients: `10.200.0.2+/32`.
- Client `AllowedIPs` (client-side config): `192.168.1.0/24, 10.200.0.0/24`.
- **Client → admin VM:** client → `wg0` → VM forwards to `10.100.0.1`
  (Proxmox) → admin `/27`s. Replies return via the existing
  `10.200.0.0/24 via 10.100.0.2` route. Reply source is the admin IP the
  request landed on (`192.168.1.x`), which is in the client's `AllowedIPs`. ✓
- **Admin VM → client:** without help this fails — a VM's default route is its
  production NIC, so it would send with a `192.168.0.x` source that the client
  rejects (not in `AllowedIPs`), and WireGuard cryptokey routing drops it.
  Fix: **masquerade tunnel-bound traffic to `10.200.0.1`** on the VPN VM, so
  every packet reaching a client has a source inside its `AllowedIPs`.
  Trade-off: clients see `10.200.0.1`, not the real source. Accepted.

No masquerade toward the internet (admin-only scope, not a full tunnel).

## Components

### `playbooks/wireguard.yaml`

Mirrors the existing playbook style (`vaultwarden.yaml`):

```yaml
---
- hosts: wireguard
  become: yes
  vars:
    sshd_listen: "{{ ansible_all_ipv4_addresses | ansible.netcommon.ipaddr(admin_network) | first }}"
  roles:
    - common
    - wireguard
```

No `install_docker`. `sshd_listen` binds SSH to the admin NIC (`10.100.0.2`),
consistent with the vaultwarden host and matching how Ansible connects.

### `inventories/group_vars/wireguard/vars.yaml`

```yaml
---
private_network: "10.0.0.0/24"
admin_network: "10.100.0.0/24"

group_hosts: {}   # VPN VM can't reach the 192.168.0.x production plane; no /etc/hosts fleet entries needed

# --- WireGuard ---
wireguard_listen_port: 51820
wireguard_address: "10.200.0.1/24"        # server wg0
wireguard_client_subnet: "10.200.0.0/24"
wireguard_admin_route: "192.168.1.0/24"   # pushed to clients + routed on the VM
wireguard_admin_gateway: "10.100.0.1"     # Proxmox admin-side gateway (vmbr2.100)

wireguard_server_public_key: "<PUBLIC KEY — filled in after key generation>"

# Declarative peers. Clients generate their own keypair; only the public key
# and the assigned tunnel IP live here.
wireguard_peers:
  - name: enzo-laptop
    public_key: "<client public key>"
    ip: "10.200.0.2/32"
  # - name: enzo-phone
  #   public_key: "..."
  #   ip: "10.200.0.3/32"
```

### `inventories/group_vars/wireguard/vault.yaml`

Encrypted with the repo's existing vault tooling (`encrypt-vault-values.py` /
`vault-pass.sh`), same style as other groups:

```yaml
wireguard_server_private_key: "<vaulted>"
# Optional per-peer preshared keys keyed by peer name, if used later.
```

### `roles/wireguard/`

```
roles/wireguard/
  defaults/main.yml      # role_wireguard_played: false; sane WG defaults
  handlers/main.yml      # "Sync wireguard config" -> wg syncconf (no flap)
  tasks/main.yml         # include tasks.yml when not role_wireguard_played
  tasks/tasks.yml
  templates/wg0.conf.j2
```

**`tasks/tasks.yml`:**

1. Install `wireguard` (apt). Debian 12 bookworm; kernel module is built in,
   the package provides `wg`/`wg-quick`.
2. Persist forwarding: `ansible.posix.sysctl net.ipv4.ip_forward=1`
   (`sysctl_set: yes`, `reload: yes`). `ansible.posix` is installed in the venv;
   add it to `requirements.yml` collections for reproducibility.
3. Template `/etc/wireguard/wg0.conf`, owner root, mode `0600`, notify the
   sync handler.
4. `systemd`: enable + start `wg-quick@wg0`.

**`templates/wg0.conf.j2`:**

```ini
# Ansible-managed — do not edit by hand
[Interface]
Address = {{ wireguard_address }}
ListenPort = {{ wireguard_listen_port }}
PrivateKey = {{ wireguard_server_private_key }}

# Route the admin plane out the admin NIC (idempotent)
PostUp   = ip route replace {{ wireguard_admin_route }} via {{ wireguard_admin_gateway }}
# Forward tunnel traffic (no physical NIC name hardcoded)
PostUp   = iptables -A FORWARD -i %i -j ACCEPT
PostUp   = iptables -A FORWARD -o %i -j ACCEPT
# Masquerade tunnel-bound traffic so VMs can initiate to clients
PostUp   = iptables -t nat -A POSTROUTING -o %i -j MASQUERADE

PostDown = iptables -t nat -D POSTROUTING -o %i -j MASQUERADE
PostDown = iptables -D FORWARD -o %i -j ACCEPT
PostDown = iptables -D FORWARD -i %i -j ACCEPT

{% for peer in wireguard_peers %}
[Peer]
# {{ peer.name }}
PublicKey = {{ peer.public_key }}
AllowedIPs = {{ peer.ip }}
{% endfor %}
```

**Handler** — reload without dropping the tunnel:

```yaml
- name: Sync wireguard config
  become: yes
  ansible.builtin.shell: "wg syncconf wg0 <(wg-quick strip wg0)"
  args:
    executable: /bin/bash
```

(`syncconf` applies peer changes live; `PostUp`/route/iptables changes need a
`systemctl restart wg-quick@wg0` — noted in the runbook.)

## Key generation (one-time)

```bash
umask 077
wg genkey | tee server.key | wg pubkey > server.pub
```

- `server.key` → `wireguard_server_private_key` in `vault.yaml` (via
  `encrypt-vault-values.py`).
- `server.pub` → `wireguard_server_public_key` in `vars.yaml`.
- Delete the plaintext key files afterward.

## Client onboarding (per client, documented — not automated)

On the client:

```bash
wg genkey | tee client.key | wg pubkey > client.pub
```

Add `{name, <client.pub>, 10.200.0.N/32}` to `wireguard_peers`, re-run the
playbook. Client `wg0.conf`:

```ini
[Interface]
PrivateKey = <client.key>
Address = 10.200.0.N/32

[Peer]
PublicKey = <wireguard_server_public_key>
Endpoint = <public-ip-or-dns>:51820
AllowedIPs = 192.168.1.0/24, 10.200.0.0/24
PersistentKeepalive = 25
```

## Testing / verification

1. `ansible-playbook playbooks/wireguard.yaml --check --diff` (config diff).
2. Apply; on the VM: `wg show`, `ip route`, `iptables -t nat -L POSTROUTING`.
3. Add one real peer, bring up a client, confirm handshake (`wg show` latest
   handshake) and reach an admin IP (e.g. `ssh` / ping an admin `192.168.1.x`).
4. From an admin VM, confirm it can reach the client's `10.200.0.N` (validates
   the masquerade path).

## Out of scope / assumptions

- Proxmox-host inbound UDP DNAT (manual, above).
- No full-internet tunnel; no production-plane (`192.168.0.0/24`) access.
- Assumes Debian 12 on the VM and default `FORWARD` policy `ACCEPT`
  (no ufw/docker on this host); explicit `wg0` FORWARD rules added regardless.
- Client keypairs never leave the client; only public keys enter the repo.
