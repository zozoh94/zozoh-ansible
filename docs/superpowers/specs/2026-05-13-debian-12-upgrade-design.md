# Debian 11 → 12 Upgrade Playbook — Design

## Goal

A single Ansible playbook that performs an in-place distribution upgrade of a managed host from Debian 11 (bullseye) to Debian 12 (bookworm). One host per invocation, fully unattended after the snapshot confirmation gate.

## Scope

Hosts currently in `inventories/all.yaml`:

- `proxy1.ovh.zozoh.fr` — common_proxy
- `nextcloud.ovh.zozoh.fr`
- `gitlab.ovh.zozoh.fr` — GitLab Omnibus
- `gitlab-runner1.ovh.zozoh.fr`
- `vaultwarden.ovh.zozoh.fr`
- `web.lagabelle.ovh.zozoh.fr`

The playbook is host-agnostic — it does not encode service-specific logic. It targets `hosts: all` and is driven per-host with `--limit`.

## Invocation

```
ansible-playbook playbooks/upgrade-debian.yaml \
    --limit <host> \
    -e confirm_upgrade=yes
```

The `confirm_upgrade=yes` extra var is a required guard: the playbook asserts its presence and aborts otherwise. This prevents a stray `ansible-playbook playbooks/upgrade-debian.yaml` from upgrading every host.

`--limit` accepts a single host or group. `serial: 1` ensures sequential processing even if multiple match.

## Required configuration

### Variables (non-secret) — `inventories/group_vars/all.yaml`

- `proxmox_api_host` — e.g. `ovh.zozoh.fr`
- `proxmox_api_port` — e.g. `8006` (Proxmox web/API port)
- `proxmox_api_user` — e.g. `root@pam` or a dedicated API user
- `proxmox_node` — Proxmox node name (e.g. `pve`)
- `proxmox_api_validate_certs` — `true` / `false` depending on TLS setup

### Variables (secret) — vault-encrypted

- `proxmox_api_token_id` — token id of the API user
- `proxmox_api_token_secret` — token secret

Stored in a vault-encrypted file (the project already uses `vault-pass.sh` and per-group vault folders like `inventories/group_vars/vaultwarden/`). The plan adds these to a new `inventories/group_vars/all/vault.yaml` and converts `inventories/group_vars/all.yaml` into a directory so the existing global vars and the new vault vars coexist under `all/`.

### Per-host — `inventories/all.yaml`

Each host gets a `proxmox_vmid` attribute, attached inline as a host var:

```yaml
gitlab.ovh.zozoh.fr:
  proxmox_vmid: 101
```

### Collection — `requirements.yml`

Add `community.general` (provides `community.general.proxmox_snap`).

## Play structure

Single play, `become: yes`, `hosts: all`, `serial: 1`, `any_errors_fatal: true`.

### Phase 1 — Pre-flight (fail-fast)

1. Assert `confirm_upgrade == 'yes'`.
2. Skip-with-message if `ansible_distribution_major_version == '12'` (already done — idempotent re-run is a no-op).
3. Assert `ansible_distribution == 'Debian'` and major version `== '11'`.
4. Assert free space on `/` ≥ 2 GB and on `/var` ≥ 3 GB (via `ansible_mounts`).
5. Run `dpkg --audit` — abort if it produces output (broken packages).
6. Run `apt-mark showhold` — abort if non-empty.
7. Assert all required Proxmox vars are set: `proxmox_api_host`, `proxmox_api_user`, `proxmox_api_token_id`, `proxmox_api_token_secret`, `proxmox_node`, `proxmox_vmid`.
8. Take a Proxmox snapshot via `community.general.proxmox_snap`, delegated to `localhost`:
   - `snapname: "pre-bookworm-{{ ansible_date_time.iso8601_basic_short }}"`
   - `description: "Auto snapshot before Debian 12 upgrade ({{ inventory_hostname }})"`
   - `vmstate: false` (disk-only — RAM snapshot is slower and not needed for rollback)
   - `state: present`, `timeout: 300`
   - Fails fast if the snapshot fails. The snapshot is never auto-deleted; cleanup is manual after the user verifies success.

### Phase 2 — Fully patch on bullseye first

1. `apt update`
2. `apt upgrade -y` (with `Dpkg::Options::=--force-confold`)
3. `apt full-upgrade -y` (with `--force-confold`)
4. `apt autoremove --purge -y`

### Phase 3 — Rewrite APT sources to bookworm

1. Find all `*.list` files under `/etc/apt/sources.list.d/` plus `/etc/apt/sources.list`.
2. Find all `*.sources` files (deb822 format) under `/etc/apt/sources.list.d/`.
3. For each: `replace` `bullseye` → `bookworm` (covers Docker, GitLab Omnibus, and the common role's repos).
4. Special-case security: ensure the security line for the main suite uses the new bookworm form
   `deb http://security.debian.org/debian-security bookworm-security main` (the path component `/updates` was removed between releases; a plain `bullseye` → `bookworm` replace would not catch the `bullseye-security` → `bookworm-security` suffix, but a second targeted `replace` handles it).
5. Rewrite `/etc/cloud/templates/sources.list.debian.tmpl` if present (cloud-init regenerates `/etc/hosts` and sources on reboot in some images).
6. Back up each modified file via the module's `backup: yes`.

### Phase 4 — Distribution upgrade

1. `apt update`
2. `apt upgrade --without-new-pkgs -y` with `Dpkg::Options::=--force-confold` and `DEBIAN_FRONTEND=noninteractive`
3. `apt full-upgrade -y` with the same options
4. `apt autoremove --purge -y`
5. `apt clean`

### Phase 5 — Reboot

Ansible `reboot` module, `reboot_timeout: 900`, default `post_reboot_delay`.

### Phase 6 — Post-upgrade verification

1. Re-gather facts (`setup`).
2. Assert `ansible_distribution_release == 'bookworm'` and `ansible_distribution_major_version == '12'`.
3. `apt-mark showhold` again — surface anything that didn't upgrade cleanly (most likely candidate: `gitlab-ce` if Omnibus' bookworm suite lags behind your current version).
4. `find /etc -name '*.dpkg-dist' -o -name '*.ucf-dist'` — print the list as a warning. These are config files where the new package default differs from your local version; the playbook never auto-merges them.
5. Print summary: kernel, Debian version, held packages, leftover `.dpkg-dist` files.

## Explicit non-goals

- **No service-specific reconfig.** PostgreSQL, GitLab, Docker, Nextcloud, Vaultwarden roles are not re-run. After the OS upgrade succeeds, the existing per-service playbook (`gitlab.yaml`, `nextcloud.yaml`, etc.) is run separately to re-converge.
- **No snapshot cleanup.** The pre-bookworm snapshot stays on Proxmox until the user deletes it manually.
- **No third-party repo addition.** Existing repos are rewritten; no new repos are introduced.
- **No unattended-upgrades reconfig.** The common role handles that on the next regular play.
- **No rollback logic.** Rollback is "restore the OVH snapshot." The playbook makes no attempt to revert.

## Risks and known failure modes

- **GitLab Omnibus bookworm availability.** The omnibus apt repo file pinned to `bullseye` will be rewritten to `bookworm`. If the currently-installed `gitlab-ce` version was never published to the bookworm suite, the dist-upgrade will hold it. Surfaced by the post-upgrade `apt-mark showhold` check; resolved by bumping GitLab separately via `gitlab.yaml`.
- **Docker repo.** Docker's apt repo supports bookworm; the rewrite is safe.
- **Custom configs with `--force-confold`.** Local config wins; new defaults end up in `.dpkg-dist`. The post-upgrade report surfaces these for manual review.
- **Cloud-init template drift.** Rewriting `/etc/cloud/templates/sources.list.debian.tmpl` matters only if the image is rebuilt from cloud-init; failing to update it would silently reintroduce bullseye sources on rebuild.

## File layout

- New: `playbooks/upgrade-debian.yaml` — the playbook itself.
- New: `inventories/group_vars/all/vault.yaml` — vault-encrypted Proxmox API token (requires converting `inventories/group_vars/all.yaml` to a directory `inventories/group_vars/all/` with the existing content moved to `inventories/group_vars/all/vars.yaml`, and the new Proxmox non-secret defaults added there).
- Modified: `inventories/all.yaml` — add `proxmox_vmid: <int>` to each host.
- Modified: `requirements.yml` — add `community.general` collection under a new `collections:` key.
- No new roles.

## Test plan

- **Dry-run check:** `ansible-playbook playbooks/upgrade-debian.yaml --limit <host> -e confirm_upgrade=yes --check` runs through pre-flight assertions without making changes. (Limitation: `apt` tasks won't truly dry-run a dist-upgrade in check mode; this only validates the pre-flight phase.)
- **First real run:** the lowest-risk host (`gitlab-runner1.ovh.zozoh.fr` — stateless executor) after taking a snapshot.
- **Verification after each run:** `ansible -i inventories <host> -m setup -a 'filter=ansible_distribution_release'` returns `bookworm`.
