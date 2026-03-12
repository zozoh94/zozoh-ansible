# Mailserver Role

Ansible role to deploy a complete mail server with Postfix, Dovecot, OpenDKIM, OpenDMARC, and SpamAssassin on Debian 12 (Bookworm).

## Features

- **Postfix** — SMTP with virtual domains/mailboxes stored in PostgreSQL
- **Dovecot** — IMAP/POP3/LMTP/Sieve with SQL authentication (BLF-CRYPT)
- **OpenDKIM** — DKIM signing per domain (auto key generation)
- **OpenDMARC** — DMARC verification
- **SpamAssassin** — Spam filtering via Postfix pipe transport
- **Let's Encrypt** — Automatic TLS certificate provisioning
- **OVH DNS** — Automatic DNS record creation (MX, SPF, DKIM, DMARC, autoconfig)

## Requirements

- Debian 12 (Bookworm) target host
- `community.postgresql` Ansible collection (`ansible-galaxy collection install community.postgresql`)
- OVH API credentials (if using DNS automation)

## Role Variables

See `defaults/main.yml` for all available variables. Key ones:

```yaml
# Required
mailserver_domain: "example.com"
mailserver_hostname: "mail.example.com"
mailserver_db_password: "secure_db_password"

# Mail domains
mailserver_domains:
  - "example.com"
  - "another-domain.com"

# Mailboxes
mailserver_mailboxes:
  - username: "user@example.com"
    password: "$2b$12$..."   # BLF-CRYPT hash (use doveadm pw -s BLF-CRYPT)
    name: "User Name"

# Aliases
mailserver_aliases:
  - address: "postmaster@example.com"
    goto: "user@example.com"

# OVH DNS (optional)
mailserver_ovh_dns_enabled: true
mailserver_ovh_application_key: "your_app_key"
mailserver_ovh_application_secret: "your_app_secret"
mailserver_ovh_consumer_key: "your_consumer_key"
```

## Generating Password Hashes

Use `doveadm` to generate BLF-CRYPT password hashes:

```bash
doveadm pw -s BLF-CRYPT -p "your_password"
```

## OVH API Credentials

To generate OVH API credentials for DNS management:

1. Go to https://eu.api.ovh.com/createToken/
2. Log in with your OVH account
3. Fill in the form:
   - **Application name**: `ansible-mailserver`
   - **Application description**: `Ansible mail server DNS management`
   - **Validity**: `Unlimited`
   - **Rights**:
     - `GET /domain/zone/*`
     - `POST /domain/zone/*`
     - `PUT /domain/zone/*`
     - `DELETE /domain/zone/*`
4. Click **Create keys**
5. Note down the **Application Key**, **Application Secret**, and **Consumer Key**
6. Set them in your inventory (use Ansible Vault for secrets):

```yaml
mailserver_ovh_application_key: "your_app_key"
mailserver_ovh_application_secret: !vault |
  $ANSIBLE_VAULT;1.1;AES256
  ...
mailserver_ovh_consumer_key: !vault |
  $ANSIBLE_VAULT;1.1;AES256
  ...
```

## DNS Records Created

For each domain, the role creates:

| Record | Type | Value |
|--------|------|-------|
| `mail.domain.tld` | A | Server public IP |
| `domain.tld` | MX | `10 mail.domain.tld.` |
| `domain.tld` | TXT | `v=spf1 mx ~all` |
| `mail._domainkey.domain.tld` | TXT | DKIM public key |
| `_dmarc.domain.tld` | TXT | DMARC policy |
| `autoconfig.domain.tld` | CNAME | `mail.domain.tld.` |

## Ports

| Port | Service | Protocol |
|------|---------|----------|
| 25 | SMTP | STARTTLS |
| 587 | Submission | STARTTLS |
| 465 | SMTPS | TLS |
| 2525 | Alt. Submission | STARTTLS |
| 143 | IMAP | STARTTLS |
| 993 | IMAPS | TLS |
| 110 | POP3 | STARTTLS |
| 995 | POP3S | TLS |

## Example Playbook

```yaml
---
- hosts: mail
  become: yes
  roles:
    - common
    - mailserver
```

## Inventory Example

```yaml
all:
  children:
    mail:
      hosts:
        mailserver:
          ansible_host: mail.ovh.zozoh.fr
          mailserver_domain: zozoh.fr
          mailserver_domains:
            - zozoh.fr
            - urbvm.fr
          mailserver_db_password: !vault |
            ...
          mailserver_ovh_dns_enabled: true
```
