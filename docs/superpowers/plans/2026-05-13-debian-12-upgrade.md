# Debian 11 → 12 Upgrade Playbook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single Ansible playbook that performs an in-place Debian 11 → 12 upgrade on one host at a time, gated by a confirmation flag and bracketed by an automated Proxmox snapshot and a post-reboot verification.

**Architecture:** One self-contained playbook at `playbooks/upgrade-debian.yaml` driven by `--limit`. Six sequential phases (pre-flight, patch-on-bullseye, sources rewrite, dist-upgrade, reboot, verify). Proxmox snapshot is taken via the `community.general.proxmox_snap` module delegated to localhost.

**Tech Stack:** Ansible (`./venv/bin/ansible-playbook`), `community.general` collection, Proxmox API, vault-encrypted secrets via the existing `vault-pass.sh` script.

**Spec reference:** `docs/superpowers/specs/2026-05-13-debian-12-upgrade-design.md`

---

## Working environment

All `ansible*` commands run from the repo root (`/home/eahameli/perso/zozoh-ansible`) using the venv:

```
./venv/bin/ansible-playbook
./venv/bin/ansible-galaxy
./venv/bin/ansible-vault
```

The repo's `ansible.cfg` already wires `vault-pass.sh` as the vault password file, so no extra flags are needed.

## File map

- Create: `inventories/group_vars/all/vars.yaml` (from current `inventories/group_vars/all.yaml`, plus Proxmox non-secret config)
- Create: `inventories/group_vars/all/vault.yaml` (vaulted `proxmox_api_token_id` + `proxmox_api_token_secret`)
- Delete: `inventories/group_vars/all.yaml` (replaced by the directory above)
- Modify: `inventories/all.yaml` (add `proxmox_vmid: <int>` per host)
- Modify: `requirements.yml` (add `collections:` section with `community.general`)
- Create: `playbooks/upgrade-debian.yaml` (the playbook)

---

## Task 1: Add the `community.general` collection

**Files:**
- Modify: `requirements.yml`

- [ ] **Step 1: Update `requirements.yml`**

Replace the entire content of `requirements.yml` with:

```yaml
---
roles:
  - name: geerlingguy.redis
  - name: geerlingguy.postgresql
  - name: geerlingguy.java
  - name: geerlingguy.elasticsearch
  - name: geerlingguy.rabbitmq
  - name: geerlingguy.gitlab
  - name: geerlingguy.nginx
  - name: geerlingguy.certbot
  - name: riemers.gitlab-runner

collections:
  - name: community.general
```

- [ ] **Step 2: Install the collection**

Run: `./venv/bin/ansible-galaxy collection install -r requirements.yml`
Expected: prints `community.general` installed (or "already installed"), exit 0.

- [ ] **Step 3: Verify the module is reachable**

Run: `./venv/bin/ansible-doc community.general.proxmox_snap | head -5`
Expected: prints module documentation header (not an error).

- [ ] **Step 4: Commit**

```bash
git add requirements.yml
git commit -m "chore: add community.general collection for Proxmox snapshots"
```

---

## Task 2: Convert `inventories/group_vars/all.yaml` to a directory

The repo's convention for groups with secrets is a folder with `vars.yaml` + `vault.yaml` (see `inventories/group_vars/nextcloud/`). Apply that pattern to `all` so the new vault file has a place to live, then add Proxmox non-secret defaults.

**Files:**
- Delete: `inventories/group_vars/all.yaml`
- Create: `inventories/group_vars/all/vars.yaml`

- [ ] **Step 1: Move the existing file into a directory**

```bash
mkdir -p inventories/group_vars/all
git mv inventories/group_vars/all.yaml inventories/group_vars/all/vars.yaml
```

- [ ] **Step 2: Append Proxmox non-secret defaults to `vars.yaml`**

Append the following block to the bottom of `inventories/group_vars/all/vars.yaml`:

```yaml

# Proxmox API used by playbooks/upgrade-debian.yaml.
# Per-host VMIDs live as host vars in inventories/all.yaml.
# Secrets (token id + secret) are in inventories/group_vars/all/vault.yaml.
proxmox_api_host: ovh.zozoh.fr
proxmox_api_port: 8006
proxmox_api_user: root@pam
proxmox_node: pve
proxmox_api_validate_certs: true
```

(Adjust `proxmox_node` if your node isn't named `pve` — placeholder per spec.)

- [ ] **Step 3: Verify Ansible still parses the inventory**

Run: `./venv/bin/ansible-inventory --list -y > /dev/null && echo OK`
Expected: prints `OK`, exit 0.

- [ ] **Step 4: Commit**

```bash
git add inventories/group_vars/all/ inventories/group_vars/all.yaml
git commit -m "refactor: split group_vars/all into directory and add Proxmox defaults"
```

---

## Task 3: Add the vaulted Proxmox API token

**Files:**
- Create: `inventories/group_vars/all/vault.yaml`

- [ ] **Step 1: Generate the encrypted token id**

The Proxmox API token format is `USER@REALM!TOKENID`. Example: `root@pam!ansible`.

Replace `REPLACE_WITH_TOKEN_ID` below with your actual token id string, then run:

```bash
./venv/bin/ansible-vault encrypt_string 'REPLACE_WITH_TOKEN_ID' --name proxmox_api_token_id
```

Copy the multi-line `proxmox_api_token_id: !vault |` output.

- [ ] **Step 2: Generate the encrypted token secret**

Replace `REPLACE_WITH_TOKEN_SECRET` below with your actual token secret (UUID-shaped), then run:

```bash
./venv/bin/ansible-vault encrypt_string 'REPLACE_WITH_TOKEN_SECRET' --name proxmox_api_token_secret
```

Copy the multi-line `proxmox_api_token_secret: !vault |` output.

- [ ] **Step 3: Write `inventories/group_vars/all/vault.yaml`**

Create the file with this content, substituting the two encrypted blocks from steps 1 and 2:

```yaml
---
proxmox_api_token_id: !vault |
          $ANSIBLE_VAULT;1.1;AES256
          <paste encrypted id here, keep indentation>

proxmox_api_token_secret: !vault |
          $ANSIBLE_VAULT;1.1;AES256
          <paste encrypted secret here, keep indentation>
```

- [ ] **Step 4: Verify decryption round-trips**

Run: `./venv/bin/ansible -i inventories -m debug -a 'var=proxmox_api_token_id' localhost`
Expected: prints the decrypted token id string (not the `!vault` block).

- [ ] **Step 5: Commit**

```bash
git add inventories/group_vars/all/vault.yaml
git commit -m "chore: add vaulted Proxmox API token"
```

---

## Task 4: Add per-host `proxmox_vmid`

**Files:**
- Modify: `inventories/all.yaml`

- [ ] **Step 1: Replace the inventory with VMID-annotated hosts**

Replace the entire content of `inventories/all.yaml` with the following, **substituting the real VMIDs from your Proxmox UI** in place of the `<vmid-...>` placeholders:

```yaml
---
all:
  children:
    common:
      children:
        common_proxy:
          hosts:
            proxy1.ovh.zozoh.fr:
              proxmox_vmid: <vmid-proxy1>
    nextcloud:
      children:
        nextcloud_web:
          hosts:
            nextcloud.ovh.zozoh.fr:
              proxmox_vmid: <vmid-nextcloud>
    gitlab:
      children:
        gitlab_webserver:
          hosts:
            gitlab.ovh.zozoh.fr:
              proxmox_vmid: <vmid-gitlab>
        gitlab_runner:
          hosts:
            gitlab-runner1.ovh.zozoh.fr:
              proxmox_vmid: <vmid-gitlab-runner1>
    vaultwarden:
      children:
        vaultwarden_web:
          hosts:
            vaultwarden.ovh.zozoh.fr:
              proxmox_vmid: <vmid-vaultwarden>
    lagabelle:
      children:
        lagabelle_web:
          hosts:
            web.lagabelle.ovh.zozoh.fr:
              proxmox_vmid: <vmid-lagabelle>
```

- [ ] **Step 2: Verify the inventory parses and each host has a VMID**

Run:
```bash
./venv/bin/ansible-inventory --list -y | grep -A1 'proxmox_vmid'
```
Expected: prints six `proxmox_vmid: <int>` lines, one per host.

- [ ] **Step 3: Commit**

```bash
git add inventories/all.yaml
git commit -m "chore: tag hosts with their Proxmox vmid"
```

---

## Task 5: Create the playbook skeleton with the safety gate and pre-flight asserts

This task lands a syntactically-valid playbook with phase 1 steps 1–7 (everything *before* the snapshot). The snapshot, the upgrade phases, the reboot, and the verification are added in tasks 6–11.

**Files:**
- Create: `playbooks/upgrade-debian.yaml`

- [ ] **Step 1: Write the skeleton**

Create `playbooks/upgrade-debian.yaml` with this content:

```yaml
---
# Debian 11 (bullseye) -> 12 (bookworm) in-place upgrade.
# Spec: docs/superpowers/specs/2026-05-13-debian-12-upgrade-design.md
#
# Invocation:
#   ansible-playbook playbooks/upgrade-debian.yaml \
#       --limit <host> -e confirm_upgrade=yes

- name: Upgrade Debian 11 -> 12
  hosts: all
  become: yes
  serial: 1
  any_errors_fatal: true
  gather_facts: yes

  vars:
    min_free_root_gb: 2
    min_free_var_gb: 3

  tasks:
    # ----- Phase 1: pre-flight -----

    - name: "Pre-flight: require confirm_upgrade=yes"
      ansible.builtin.assert:
        that:
          - confirm_upgrade | default('') == 'yes'
        fail_msg: >-
          Refusing to run without -e confirm_upgrade=yes.
          This playbook performs a destructive in-place distribution upgrade.

    - name: "Pre-flight: short-circuit if already on bookworm"
      ansible.builtin.meta: end_host
      when: ansible_distribution_major_version == '12'

    - name: "Pre-flight: verify host is on Debian 11"
      ansible.builtin.assert:
        that:
          - ansible_distribution == 'Debian'
          - ansible_distribution_major_version == '11'
        fail_msg: >-
          Expected Debian 11, found
          {{ ansible_distribution }} {{ ansible_distribution_major_version }}.

    - name: "Pre-flight: gather free space on / and /var"
      ansible.builtin.set_fact:
        free_root_gb: >-
          {{ (ansible_mounts | selectattr('mount', 'equalto', '/') | first).size_available
             / 1024 / 1024 / 1024 }}
        free_var_gb: >-
          {{ ((ansible_mounts | selectattr('mount', 'equalto', '/var') | list)
              | default([(ansible_mounts | selectattr('mount', 'equalto', '/') | first)], true)
              | first).size_available
             / 1024 / 1024 / 1024 }}

    - name: "Pre-flight: assert enough free disk"
      ansible.builtin.assert:
        that:
          - free_root_gb | float >= min_free_root_gb
          - free_var_gb | float >= min_free_var_gb
        fail_msg: >-
          Insufficient free space.
          / has {{ '%.1f' | format(free_root_gb | float) }}G (need {{ min_free_root_gb }}G),
          /var has {{ '%.1f' | format(free_var_gb | float) }}G (need {{ min_free_var_gb }}G).

    - name: "Pre-flight: dpkg --audit (must be empty)"
      ansible.builtin.command: dpkg --audit
      register: dpkg_audit
      changed_when: false

    - name: "Pre-flight: fail if dpkg reports issues"
      ansible.builtin.assert:
        that:
          - dpkg_audit.stdout | trim | length == 0
        fail_msg: |
          dpkg --audit reported issues; resolve before upgrading:
          {{ dpkg_audit.stdout }}

    - name: "Pre-flight: apt-mark showhold (must be empty)"
      ansible.builtin.command: apt-mark showhold
      register: apt_holds
      changed_when: false

    - name: "Pre-flight: fail if any package is held"
      ansible.builtin.assert:
        that:
          - apt_holds.stdout | trim | length == 0
        fail_msg: |
          Held packages would block the upgrade:
          {{ apt_holds.stdout }}

    - name: "Pre-flight: require Proxmox snapshot vars"
      ansible.builtin.assert:
        that:
          - proxmox_api_host is defined and proxmox_api_host | length > 0
          - proxmox_api_user is defined and proxmox_api_user | length > 0
          - proxmox_api_token_id is defined and proxmox_api_token_id | length > 0
          - proxmox_api_token_secret is defined and proxmox_api_token_secret | length > 0
          - proxmox_node is defined and proxmox_node | length > 0
          - proxmox_vmid is defined
        fail_msg: >-
          Missing one of the required Proxmox vars
          (proxmox_api_host, proxmox_api_user, proxmox_api_token_id,
           proxmox_api_token_secret, proxmox_node, proxmox_vmid).
```

- [ ] **Step 2: Syntax-check the playbook**

Run: `./venv/bin/ansible-playbook --syntax-check playbooks/upgrade-debian.yaml`
Expected: `playbook: playbooks/upgrade-debian.yaml`, exit 0.

- [ ] **Step 3: Confirm the gate refuses to run without `confirm_upgrade`**

Run: `./venv/bin/ansible-playbook playbooks/upgrade-debian.yaml --limit gitlab-runner1.ovh.zozoh.fr --check`
Expected: task `Pre-flight: require confirm_upgrade=yes` fails with the configured `fail_msg`. (It's fine if the run errors before reaching the host — the assertion message must be the cause.)

- [ ] **Step 4: Commit**

```bash
git add playbooks/upgrade-debian.yaml
git commit -m "feat(upgrade): scaffold debian 11 -> 12 playbook with pre-flight checks"
```

---

## Task 6: Add the Proxmox snapshot step

**Files:**
- Modify: `playbooks/upgrade-debian.yaml`

- [ ] **Step 1: Append the snapshot task**

Append the following block at the bottom of `playbooks/upgrade-debian.yaml` (after the "require Proxmox snapshot vars" task, still inside the `tasks:` list — preserve the existing indentation, two spaces for `-`):

```yaml

    - name: "Pre-flight: take Proxmox snapshot"
      community.general.proxmox_snap:
        api_host: "{{ proxmox_api_host }}"
        api_port: "{{ proxmox_api_port | default(8006) }}"
        api_user: "{{ proxmox_api_user }}"
        api_token_id: "{{ proxmox_api_token_id }}"
        api_token_secret: "{{ proxmox_api_token_secret }}"
        validate_certs: "{{ proxmox_api_validate_certs | default(true) }}"
        node: "{{ proxmox_node }}"
        vmid: "{{ proxmox_vmid }}"
        snapname: "pre-bookworm-{{ ansible_date_time.iso8601_basic_short }}"
        description: "Auto snapshot before Debian 12 upgrade ({{ inventory_hostname }})"
        vmstate: false
        timeout: 300
        state: present
      delegate_to: localhost
      become: false
```

- [ ] **Step 2: Syntax-check**

Run: `./venv/bin/ansible-playbook --syntax-check playbooks/upgrade-debian.yaml`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add playbooks/upgrade-debian.yaml
git commit -m "feat(upgrade): take Proxmox snapshot before dist-upgrade"
```

---

## Task 7: Add Phase 2 — fully patch on bullseye

**Files:**
- Modify: `playbooks/upgrade-debian.yaml`

- [ ] **Step 1: Append phase 2 tasks**

Append at the bottom of the `tasks:` list:

```yaml

    # ----- Phase 2: fully patch on bullseye -----

    - name: "Phase 2: apt update (bullseye)"
      ansible.builtin.apt:
        update_cache: yes

    - name: "Phase 2: apt upgrade (bullseye)"
      ansible.builtin.apt:
        upgrade: 'yes'
      environment:
        DEBIAN_FRONTEND: noninteractive

    - name: "Phase 2: apt full-upgrade (bullseye)"
      ansible.builtin.apt:
        upgrade: 'dist'
      environment:
        DEBIAN_FRONTEND: noninteractive

    - name: "Phase 2: apt autoremove (bullseye)"
      ansible.builtin.apt:
        autoremove: yes
        purge: yes
```

- [ ] **Step 2: Syntax-check**

Run: `./venv/bin/ansible-playbook --syntax-check playbooks/upgrade-debian.yaml`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add playbooks/upgrade-debian.yaml
git commit -m "feat(upgrade): fully patch on bullseye before dist-upgrade"
```

---

## Task 8: Add Phase 3 — rewrite APT sources to bookworm

**Files:**
- Modify: `playbooks/upgrade-debian.yaml`

- [ ] **Step 1: Append phase 3 tasks**

Append at the bottom of the `tasks:` list:

```yaml

    # ----- Phase 3: rewrite APT sources bullseye -> bookworm -----

    - name: "Phase 3: list APT sources files (.list)"
      ansible.builtin.find:
        paths:
          - /etc/apt
          - /etc/apt/sources.list.d
        patterns:
          - "*.list"
        file_type: file
        recurse: no
      register: apt_list_files

    - name: "Phase 3: list APT sources files (.sources, deb822)"
      ansible.builtin.find:
        paths:
          - /etc/apt/sources.list.d
        patterns:
          - "*.sources"
        file_type: file
        recurse: no
      register: apt_sources_files

    - name: "Phase 3: rewrite bullseye -> bookworm in all source files"
      ansible.builtin.replace:
        path: "{{ item.path }}"
        regexp: '\bbullseye\b'
        replace: 'bookworm'
        backup: yes
      loop: "{{ (apt_list_files.files + apt_sources_files.files) }}"
      loop_control:
        label: "{{ item.path }}"

    - name: "Phase 3: ensure security suite uses bookworm-security URL"
      ansible.builtin.replace:
        path: /etc/apt/sources.list
        regexp: 'http://security\.debian\.org/debian-security\s+bookworm/updates'
        replace: 'http://security.debian.org/debian-security bookworm-security'
        backup: yes

    - name: "Phase 3: check for cloud-init sources template"
      ansible.builtin.stat:
        path: /etc/cloud/templates/sources.list.debian.tmpl
      register: cloud_sources_tmpl

    - name: "Phase 3: rewrite bullseye -> bookworm in cloud-init template"
      ansible.builtin.replace:
        path: /etc/cloud/templates/sources.list.debian.tmpl
        regexp: '\bbullseye\b'
        replace: 'bookworm'
        backup: yes
      when: cloud_sources_tmpl.stat.exists
```

- [ ] **Step 2: Syntax-check**

Run: `./venv/bin/ansible-playbook --syntax-check playbooks/upgrade-debian.yaml`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add playbooks/upgrade-debian.yaml
git commit -m "feat(upgrade): rewrite APT sources from bullseye to bookworm"
```

---

## Task 9: Add Phase 4 — distribution upgrade

**Files:**
- Modify: `playbooks/upgrade-debian.yaml`

- [ ] **Step 1: Append phase 4 tasks**

Append at the bottom of the `tasks:` list:

```yaml

    # ----- Phase 4: distribution upgrade -----

    - name: "Phase 4: apt update (bookworm)"
      ansible.builtin.apt:
        update_cache: yes

    - name: "Phase 4: minimal upgrade without new packages"
      ansible.builtin.apt:
        upgrade: 'yes'
        dpkg_options: 'force-confold,force-confdef'
      environment:
        DEBIAN_FRONTEND: noninteractive

    - name: "Phase 4: full-upgrade to bookworm"
      ansible.builtin.apt:
        upgrade: 'dist'
        dpkg_options: 'force-confold,force-confdef'
      environment:
        DEBIAN_FRONTEND: noninteractive

    - name: "Phase 4: apt autoremove"
      ansible.builtin.apt:
        autoremove: yes
        purge: yes

    - name: "Phase 4: apt clean"
      ansible.builtin.command: apt-get clean
      changed_when: false
```

- [ ] **Step 2: Syntax-check**

Run: `./venv/bin/ansible-playbook --syntax-check playbooks/upgrade-debian.yaml`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add playbooks/upgrade-debian.yaml
git commit -m "feat(upgrade): run dist-upgrade to bookworm"
```

---

## Task 10: Add Phase 5 — reboot and wait

**Files:**
- Modify: `playbooks/upgrade-debian.yaml`

- [ ] **Step 1: Append phase 5 task**

Append at the bottom of the `tasks:` list:

```yaml

    # ----- Phase 5: reboot -----

    - name: "Phase 5: reboot and wait for host to return"
      ansible.builtin.reboot:
        reboot_timeout: 900
        msg: "Rebooting after Debian 12 upgrade"
```

- [ ] **Step 2: Syntax-check**

Run: `./venv/bin/ansible-playbook --syntax-check playbooks/upgrade-debian.yaml`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add playbooks/upgrade-debian.yaml
git commit -m "feat(upgrade): reboot and wait after dist-upgrade"
```

---

## Task 11: Add Phase 6 — post-upgrade verification

**Files:**
- Modify: `playbooks/upgrade-debian.yaml`

- [ ] **Step 1: Append phase 6 tasks**

Append at the bottom of the `tasks:` list:

```yaml

    # ----- Phase 6: post-upgrade verification -----

    - name: "Phase 6: re-gather facts"
      ansible.builtin.setup:

    - name: "Phase 6: assert we are on bookworm"
      ansible.builtin.assert:
        that:
          - ansible_distribution == 'Debian'
          - ansible_distribution_major_version == '12'
          - ansible_distribution_release == 'bookworm'
        fail_msg: >-
          Post-upgrade verification failed:
          {{ ansible_distribution }} {{ ansible_distribution_major_version }}
          ({{ ansible_distribution_release }}).

    - name: "Phase 6: re-check held packages"
      ansible.builtin.command: apt-mark showhold
      register: post_holds
      changed_when: false

    - name: "Phase 6: find leftover .dpkg-dist / .ucf-dist files"
      ansible.builtin.find:
        paths: /etc
        patterns:
          - "*.dpkg-dist"
          - "*.ucf-dist"
        recurse: yes
        file_type: file
      register: leftover_configs

    - name: "Phase 6: summary"
      ansible.builtin.debug:
        msg:
          - "Host: {{ inventory_hostname }}"
          - "Distribution: {{ ansible_distribution }} {{ ansible_distribution_version }} ({{ ansible_distribution_release }})"
          - "Kernel: {{ ansible_kernel }}"
          - "Held packages: {{ post_holds.stdout_lines | default([]) }}"
          - "Leftover configs (review and merge manually): {{ leftover_configs.files | map(attribute='path') | list }}"
```

- [ ] **Step 2: Syntax-check**

Run: `./venv/bin/ansible-playbook --syntax-check playbooks/upgrade-debian.yaml`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add playbooks/upgrade-debian.yaml
git commit -m "feat(upgrade): verify bookworm and report leftover configs"
```

---

## Task 12: Real-run on the lowest-risk host

This is the integration test. Only run after the per-host VMID for `gitlab-runner1.ovh.zozoh.fr` is filled in and the Proxmox API token is configured. `gitlab-runner1` is chosen because it is stateless (re-registering a runner is cheap) per the spec's test plan.

- [ ] **Step 1: Confirm VMID is set and not a placeholder**

Run:
```bash
./venv/bin/ansible-inventory --host gitlab-runner1.ovh.zozoh.fr | grep proxmox_vmid
```
Expected: prints `"proxmox_vmid": <integer>` (not `<vmid-...>`).

- [ ] **Step 2: Take a manual Proxmox snapshot as a belt-and-braces backup**

This is independent of the playbook's auto-snapshot, in case the auto-snapshot itself fails or the user wants a named pre-run reference. Skip if comfortable relying only on the playbook's automated snapshot.

- [ ] **Step 3: Execute the playbook**

Run:
```bash
./venv/bin/ansible-playbook playbooks/upgrade-debian.yaml \
    --limit gitlab-runner1.ovh.zozoh.fr \
    -e confirm_upgrade=yes
```

Expected: each phase prints `ok`/`changed` tasks, the reboot returns, and the final `Phase 6: summary` debug shows `bookworm` and `Debian 12`. Total runtime ~20–40 minutes.

- [ ] **Step 4: Verify from outside**

Run:
```bash
./venv/bin/ansible -i inventories gitlab-runner1.ovh.zozoh.fr \
    -m setup -a 'filter=ansible_distribution_release'
```
Expected: `ansible_distribution_release: bookworm`.

- [ ] **Step 5: Re-run the per-service playbook to re-converge roles**

Run:
```bash
./venv/bin/ansible-playbook playbooks/gitlab.yaml --limit gitlab-runner1.ovh.zozoh.fr
```
Expected: ends with `failed=0`. Any role-level config that needed bumping for bookworm shows up as `changed`.

- [ ] **Step 6: If everything works, the playbook is validated**

No commit — this is a runtime verification. If any phase failed, do not proceed to other hosts; investigate and patch the playbook.

---

## Out of plan

These are explicit non-goals per the spec; do not add tasks for them:

- Cleaning up Proxmox snapshots after a successful upgrade.
- Re-running the per-service playbooks automatically.
- Rolling back automatically on failure (rollback = restore the Proxmox snapshot by hand).
- Updating `unattended-upgrades` config — the existing common role handles that.

## Notes for the engineer

- The repo's `ansible.cfg` sets `vault_password_file = ./vault-pass.sh`, so vault decryption is automatic for every command run from the repo root.
- The `apt` module's `upgrade: 'yes'` maps to `apt-get upgrade --with-new-pkgs` (the modern default; bookworm release notes accept this as the "minimal" first step). `upgrade: 'dist'` maps to `apt-get dist-upgrade` / `apt full-upgrade`. There is no `--without-new-pkgs` flag for the Ansible module — `upgrade: 'yes'` is the correct first step.
- `community.general.proxmox_snap` is `delegate_to: localhost` because it's an HTTP call to the Proxmox API host, not something the target VM runs.
- Phase 6's `setup:` task re-gathers facts because reboot invalidates the cached distribution facts.
