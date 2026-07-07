# WireGuard VPN Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Configure the `vpn` VM (VMID 210) as a WireGuard server giving declared peers split-tunnel access to the fleet's admin plane (`192.168.1.0/24`), with masquerade so VMs can also initiate connections to clients.

**Architecture:** New `wireguard` Ansible role + `playbooks/wireguard.yaml`, driven by `inventories/group_vars/wireguard/`. Pure L3 routing (no internet tunnel): clients live in `10.200.0.0/24`, the VM forwards to the Proxmox admin gateway `10.100.0.1`, and masquerades tunnel-bound traffic to `10.200.0.1`. Peers are declarative (public keys only); the server private key lives in the vault.

**Tech Stack:** Ansible (ifupdown-based Debian 12 target), `wireguard`/`wg-quick`, `ansible.posix.sysctl`, `ansible.netcommon.ipaddr`, `ansible-vault` (via `vault-pass.sh` → `pass zozoh/ansible_vault`).

## Global Constraints

- Target host: `vpn.ovh.zozoh.fr`, group `wireguard`, reached as `debian@` via `ProxyJump enzo@ovh.zozoh.fr:222` (resolves to admin IP `10.100.0.2`).
- Client subnet is fixed by existing Proxmox routing: **`10.200.0.0/24`**; server wg0 = `10.200.0.1/24`.
- Routing scope is admin-only: `192.168.1.0/24`. No production-plane (`192.168.0.0/24`) and no full-internet tunnel.
- Server private key: `wireguard_server_private_key` in `inventories/group_vars/wireguard/vault.yaml` (encrypted). Public key: `wireguard_server_public_key` in `vars.yaml`. Client keypairs never enter the repo.
- Follow existing repo conventions: `become: yes` at play level, `role_<name>_played` guard pattern, `include_tasks` from `tasks/main.yml`, FQCN module names.
- Vault commands rely on `vault-pass.sh`; `ansible.cfg` already sets `vault_password_file = ./vault-pass.sh`.
- **Manual prerequisite (NOT in this plan):** inbound UDP DNAT on the Proxmox host: `iptables -t nat -A PREROUTING -i vmbr0 -p udp --dport 51820 -j DNAT --to 10.0.0.2:51820`. Live client handshake (Task 5) cannot succeed until this exists.

---

### Task 1: Add `ansible.posix` to collection requirements

**Files:**
- Modify: `requirements.yml`

**Interfaces:**
- Produces: `ansible.posix` collection available for `ansible.posix.sysctl` in Task 3.

- [ ] **Step 1: Add the collection**

Edit `requirements.yml`, under `collections:`, add `ansible.posix` beside `community.general`:

```yaml
collections:
  - name: community.general
  - name: ansible.posix
```

- [ ] **Step 2: Verify it resolves**

Run: `ansible-galaxy collection list 2>/dev/null | grep -i posix`
Expected: a line showing `ansible.posix` with a version (already installed in the venv).

- [ ] **Step 3: Commit**

```bash
git add requirements.yml
git commit -m "chore(wireguard): declare ansible.posix collection dependency"
```

---

### Task 2: Inventory vars + server keypair (vault)

**Files:**
- Create: `inventories/group_vars/wireguard/vars.yaml`
- Create: `inventories/group_vars/wireguard/vault.yaml`

**Interfaces:**
- Produces (consumed by the role template in Task 3):
  - `wireguard_listen_port` (int, `51820`)
  - `wireguard_address` (str, `"10.200.0.1/24"`)
  - `wireguard_client_subnet` (str, `"10.200.0.0/24"`)
  - `wireguard_admin_route` (str, `"192.168.1.0/24"`)
  - `wireguard_admin_gateway` (str, `"10.100.0.1"`)
  - `wireguard_server_public_key` (str)
  - `wireguard_peers` (list of `{name, public_key, ip}`)
  - `wireguard_server_private_key` (str, vaulted)
  - `private_network` (str, `"10.0.0.0/24"`), `admin_network` (str, `"10.100.0.0/24"`), `group_hosts` (dict) — consumed by the `common` role and the `sshd_listen` expression in Task 4.

- [ ] **Step 1: Generate the server keypair locally**

```bash
cd /home/eahameli/perso/zozoh-ansible
umask 077
wg genkey | tee /tmp/wg-server.key | wg pubkey > /tmp/wg-server.pub
echo "PUB: $(cat /tmp/wg-server.pub)"
```

Expected: prints `PUB: <44-char base64 ending in =>`. (If `wg` is missing locally: `sudo apt install -y wireguard-tools`.)

- [ ] **Step 2: Write `vars.yaml` with the real public key**

Create `inventories/group_vars/wireguard/vars.yaml`, substituting the `PUB` value from Step 1 into `wireguard_server_public_key`:

```yaml
---
private_network: "10.0.0.0/24"
admin_network: "10.100.0.0/24"

# VPN VM can't reach the 192.168.0.x production plane; no fleet /etc/hosts entries.
group_hosts: {}

# --- WireGuard ---
wireguard_listen_port: 51820
wireguard_address: "10.200.0.1/24"        # server wg0
wireguard_client_subnet: "10.200.0.0/24"
wireguard_admin_route: "192.168.1.0/24"   # admin plane pushed to clients + routed on VM
wireguard_admin_gateway: "10.100.0.1"     # Proxmox admin-side gateway (vmbr2.100)

wireguard_server_public_key: "PASTE_PUB_FROM_STEP_1"

# Declarative peers. Clients generate their own keypair; only the public key
# and assigned tunnel IP live here. Start empty; real peers added in Task 5.
wireguard_peers: []
```

- [ ] **Step 3: Write `vault.yaml` with the plaintext private key**

Create `inventories/group_vars/wireguard/vault.yaml`, substituting the contents of `/tmp/wg-server.key`:

```yaml
---
# Encrypt in place after writing:
#   ./encrypt-vault-values.py inventories/group_vars/wireguard/vault.yaml
wireguard_server_private_key: "PASTE_CONTENTS_OF_/tmp/wg-server.key"
```

- [ ] **Step 4: Encrypt the private key in place**

Run: `./encrypt-vault-values.py inventories/group_vars/wireguard/vault.yaml`
Expected: `✓ inventories/group_vars/wireguard/vault.yaml: 1 encrypted, 0 already encrypted (skipped)`
Then confirm the value is now a `!vault |` block:
Run: `grep -c '!vault' inventories/group_vars/wireguard/vault.yaml`
Expected: `1`

- [ ] **Step 5: Verify the vault decrypts back to the original key**

Run: `ansible localhost -m debug -a "var=wireguard_server_private_key" -e "@inventories/group_vars/wireguard/vault.yaml" 2>/dev/null | grep -o '[A-Za-z0-9+/]\{43\}='`
Expected: prints the same key string as `cat /tmp/wg-server.key`.

- [ ] **Step 6: Verify inventory vars resolve for the host**

Run: `ansible-inventory --host vpn.ovh.zozoh.fr 2>/dev/null | grep -E 'wireguard_listen_port|wireguard_address|admin_network'`
Expected: shows `51820`, `10.200.0.1/24`, `10.100.0.0/24`.

- [ ] **Step 7: Delete plaintext key files**

```bash
shred -u /tmp/wg-server.key /tmp/wg-server.pub 2>/dev/null || rm -f /tmp/wg-server.key /tmp/wg-server.pub
```

- [ ] **Step 8: Commit**

```bash
git add inventories/group_vars/wireguard/vars.yaml inventories/group_vars/wireguard/vault.yaml
git commit -m "feat(wireguard): inventory vars + vaulted server key"
```

---

### Task 3: The `wireguard` role

**Files:**
- Create: `roles/wireguard/defaults/main.yml`
- Create: `roles/wireguard/templates/wg0.conf.j2`
- Create: `roles/wireguard/handlers/main.yml`
- Create: `roles/wireguard/tasks/main.yml`
- Create: `roles/wireguard/tasks/tasks.yml`

**Interfaces:**
- Consumes: all `wireguard_*`, `private_network`, `admin_network` vars from Task 2.
- Produces: a configured, running `wg-quick@wg0` on the target; sets fact `role_wireguard_played: true`.

- [ ] **Step 1: Create `defaults/main.yml`**

```yaml
---
role_wireguard_played: false

# Overridable in group_vars; defaults keep the role runnable standalone.
wireguard_listen_port: 51820
wireguard_address: "10.200.0.1/24"
wireguard_admin_route: "192.168.1.0/24"
wireguard_admin_gateway: "10.100.0.1"
wireguard_peers: []
```

- [ ] **Step 2: Create `templates/wg0.conf.j2`**

```jinja
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

- [ ] **Step 3: Create `handlers/main.yml`**

```yaml
---
- name: Restart wireguard
  become: yes
  ansible.builtin.service:
    name: "wg-quick@wg0"
    state: restarted
```

(Config templating changes `PostUp` rules and routes, which only take effect on a full restart — so the handler restarts rather than `syncconf`. On this single-user admin VPN a brief tunnel flap on config change is acceptable.)

- [ ] **Step 4: Create `tasks/main.yml`**

```yaml
---
- name: Include wireguard tasks
  ansible.builtin.include_tasks: tasks.yml
  when: not role_wireguard_played
```

- [ ] **Step 5: Create `tasks/tasks.yml`**

```yaml
---
- name: Install wireguard
  become: yes
  ansible.builtin.apt:
    name: wireguard
    state: present
    update_cache: true

- name: Enable IPv4 forwarding
  become: yes
  ansible.posix.sysctl:
    name: net.ipv4.ip_forward
    value: "1"
    sysctl_set: yes
    state: present
    reload: yes

- name: Deploy wg0 configuration
  become: yes
  ansible.builtin.template:
    src: wg0.conf.j2
    dest: /etc/wireguard/wg0.conf
    owner: root
    group: root
    mode: "0600"
  notify: Restart wireguard

- name: Enable and start wg-quick@wg0
  become: yes
  ansible.builtin.service:
    name: "wg-quick@wg0"
    enabled: yes
    state: started

- name: Role wireguard played
  ansible.builtin.set_fact:
    role_wireguard_played: true
```

- [ ] **Step 6: Verify template renders with the inventory vars (offline)**

Run:
```bash
ansible localhost -m ansible.builtin.template \
  -a "src=roles/wireguard/templates/wg0.conf.j2 dest=/tmp/wg0.rendered.conf mode=0600" \
  -e "@inventories/group_vars/wireguard/vars.yaml" \
  -e "@inventories/group_vars/wireguard/vault.yaml" 2>/dev/null; \
  grep -E 'Address|ListenPort|MASQUERADE|ip route replace' /tmp/wg0.rendered.conf; \
  rm -f /tmp/wg0.rendered.conf
```
Expected: shows `Address = 10.200.0.1/24`, `ListenPort = 51820`, the `MASQUERADE` line, and `ip route replace 192.168.1.0/24 via 10.100.0.1`. (`PrivateKey` is populated from the vault; with `wireguard_peers: []` there are no `[Peer]` blocks.)

- [ ] **Step 7: Commit**

```bash
git add roles/wireguard/
git commit -m "feat(wireguard): role to install and configure wg-quick@wg0"
```

---

### Task 4: The playbook

**Files:**
- Create: `playbooks/wireguard.yaml`

**Interfaces:**
- Consumes: group `wireguard`, `admin_network`, roles `common` + `wireguard`.
- Produces: a runnable playbook; `--syntax-check` clean.

- [ ] **Step 1: Create `playbooks/wireguard.yaml`**

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

- [ ] **Step 2: Syntax-check the playbook**

Run: `ansible-playbook playbooks/wireguard.yaml --syntax-check`
Expected: prints the play list with no errors (exit 0).

- [ ] **Step 3: Commit**

```bash
git add playbooks/wireguard.yaml
git commit -m "feat(wireguard): playbook wiring common + wireguard roles"
```

---

### Task 5: Dry-run, apply, and verify on the VM

**Files:** none (operational task).

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: a live WireGuard server; verified forwarding + NAT.

- [ ] **Step 1: Check-mode dry run**

Run: `ansible-playbook playbooks/wireguard.yaml --check --diff --limit vpn.ovh.zozoh.fr`
Expected: connects via the jump host; shows the diff that would create `/etc/wireguard/wg0.conf` and the sysctl change. No unexpected failures. (Some tasks may warn under `--check` if they depend on prior state; note but proceed.)

- [ ] **Step 2: Apply**

Run: `ansible-playbook playbooks/wireguard.yaml --limit vpn.ovh.zozoh.fr`
Expected: `changed` for apt/sysctl/template/service; play recap `failed=0`.

- [ ] **Step 3: Verify the interface is up on the VM**

Run: `ssh debian@vpn.ovh.zozoh.fr 'sudo wg show; echo ---; ip -brief addr show wg0; echo ---; ip route get 192.168.1.193'`
Expected: `wg show` lists the interface with the server public key and `listening port: 51820`; `wg0` has `10.200.0.1/24`; the route to `192.168.1.193` goes `via 10.100.0.1`.

- [ ] **Step 4: Verify forwarding + NAT rules are active**

Run: `ssh debian@vpn.ovh.zozoh.fr 'cat /proc/sys/net/ipv4/ip_forward; sudo iptables -t nat -S POSTROUTING; sudo iptables -S FORWARD'`
Expected: `ip_forward` = `1`; a `-A POSTROUTING -o wg0 -j MASQUERADE` rule; `-A FORWARD -i wg0 -j ACCEPT` and `-A FORWARD -o wg0 -j ACCEPT`.

- [ ] **Step 5: Add a real peer**

On a client device:
```bash
umask 077; wg genkey | tee client.key | wg pubkey > client.pub; cat client.pub
```
Then edit `inventories/group_vars/wireguard/vars.yaml`, replacing `wireguard_peers: []` with:
```yaml
wireguard_peers:
  - name: enzo-laptop
    public_key: "PASTE_client.pub"
    ip: "10.200.0.2/32"
```

- [ ] **Step 6: Re-apply and confirm the peer landed**

Run: `ansible-playbook playbooks/wireguard.yaml --limit vpn.ovh.zozoh.fr && ssh debian@vpn.ovh.zozoh.fr 'sudo wg show'`
Expected: `wg show` now lists the peer's public key with `allowed ips: 10.200.0.2/32`.

- [ ] **Step 7: Bring up the client and test both directions**

Client `wg0.conf`:
```ini
[Interface]
PrivateKey = <client.key>
Address = 10.200.0.2/32

[Peer]
PublicKey = <wireguard_server_public_key from vars.yaml>
Endpoint = 37.187.24.18:51820
AllowedIPs = 192.168.1.0/24, 10.200.0.0/24
PersistentKeepalive = 25
```
Bring it up (`wg-quick up wg0`).
Expected (requires the manual Proxmox DNAT prerequisite): `sudo wg show` on the VM shows a recent handshake for the peer; from the client, `ping 192.168.1.193` (or an admin IP) succeeds; from that admin VM, `ping 10.200.0.2` succeeds (validates masquerade).

- [ ] **Step 8: Commit the peer**

```bash
git add inventories/group_vars/wireguard/vars.yaml
git commit -m "feat(wireguard): add enzo-laptop peer"
```

---

## Self-Review notes

- **Spec coverage:** routing model (Task 3 template + Task 5 verify), no-NAT-to-internet (only wg0 MASQUERADE, Step in Task 3), declarative peers (Task 2/5), vaulted server key (Task 2), server pubkey in vars (Task 2), playbook style mirroring vaultwarden (Task 4), sshd on admin NIC (Task 4), Proxmox DNAT prerequisite (Global Constraints + Task 5 Step 7), Debian 12 package (Task 3), client onboarding doc (Task 5 Steps 5/7).
- **Deviation from spec:** the handler restarts `wg-quick@wg0` instead of `wg syncconf`, because template changes include `PostUp`/route/iptables directives that `syncconf` does not apply. Simpler and correct for a single-user admin VPN; documented in Task 3 Step 3.
- **Peer AllowedIPs:** server-side per-peer `AllowedIPs` is the client's tunnel `/32` (cryptokey routing to that client); the `192.168.1.0/24` scope is set client-side (Task 5 Step 7). This is correct WireGuard semantics.
