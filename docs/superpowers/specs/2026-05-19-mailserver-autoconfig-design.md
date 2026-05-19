# Mailserver Autoconfig / Autodiscover — Design

## Goal

Every domain listed in `mailserver_domains` automatically serves working mail-client auto-configuration for:

- **Thunderbird / Mozilla ISPDB** — `https://autoconfig.<domain>/mail/config-v1.1.xml`
- **Outlook POX autodiscover** — `https://autodiscover.<domain>/autodiscover/autodiscover.xml`
- **RFC 6186 SRV** — `_imaps._tcp.<domain>` and `_submission._tcp.<domain>` (modern Thunderbird, Apple Mail, K-9)

All three are provisioned end-to-end (DNS, TLS, web server, XML) by the `mailserver` Ansible role with no manual steps after `mailserver_domains` changes.

## Background — what the legacy server does, and why we are not copying it

`legacy.ovh.zozoh.fr` serves both endpoints from a single PHP-FPM script (`/var/www/autoconfig.php`) behind one nginx vhost (`/etc/nginx/sites-enabled/autoconfig.conf`). The script reads the POST body for Outlook to echo the email back as `<LoginName>`. Several issues make this a poor template:

- `$domain = 'zozoh.fr';` is hardcoded at the top of the PHP script, so `autoconfig.lagabelle-saint-hilaire.fr` serves the wrong configuration. Latent bug.
- `<displayName><?= Zozoh ?></displayName>` references an undefined PHP constant — works by accident on Bullseye, breaks under strict PHP.
- The vhost TLS cert is `/etc/letsencrypt/live/autoconfig.lagabelle-saint-hilaire.fr/...` but serves four SNI names, three of which are not in the cert.
- The XML advertises `imap.<domain>` / `smtp.<domain>` as the connection hostnames, but the actual server TLS cert covers `zozoh.fr` + `mail.zozoh.fr`. Connections to `imap.lagabelle-saint-hilaire.fr:993` would land on a cert with a name mismatch.
- The XML's `<documentation>` block points at `https://cloud.<domain>` — no webmail exists.
- Certbot --standalone needs port 80, but nginx is already bound to 80; the legacy host has been hand-tweaked to make this work.

Our design replaces PHP-FPM with static XML rendered at deploy time, replaces HTTP-01 with DNS-01 via OVH (no port-80 fight, no service flap on renewal), and advertises only hostnames that actually match the TLS cert.

## Architecture

```
mail client                                authoritative DNS (OVH)
   │                                              ▲
   │ 1. lookup _imaps._tcp.<domain> SRV ──────────┤  (RFC 6186)
   │ 2. else GET https://autoconfig.<domain>/...  │
   │ 3. (Outlook) POST https://autodiscover...    │
   ▼                                              │
mailserver host                                   │ ovh_dns.py
 ├─ nginx :80,:443 ──── /var/www/autoconfig/      │ (extended)
 │     single vhost; root /var/www/autoconfig/$host
 │                                                │
 ├─ certbot DNS-01 (certbot-dns-ovh) ─────────────┘  same OVH creds
 │     issues *.zozoh.fr + zozoh.fr (and same per other domain)
 └─ postfix / dovecot (unchanged paths; pick cert via SNI)
```

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | DNS-01 via OVH for ACME, with wildcard certs per primary domain | Reuses existing OVH API automation. No port-80 contention with nginx. Wildcard covers `autoconfig`, `autodiscover`, `mail`, future names with no SAN-list churn. Per-domain (not one combined cert) keeps reissue churn isolated when a domain is added or removed. |
| 2 | Static autodiscover XML with empty `<LoginName>` | No PHP, no FPM, no scripting runtime to maintain. Outlook desktop falls back to the email the user typed in the wizard. |
| 3 | Advertise `mail.<primary_domain>` (i.e. `mail.zozoh.fr`) for every tenant | The host's PTR is `zozoh.fr`, MX is `mail.zozoh.fr`, cert covers both — using `mail.zozoh.fr` is the cert-matched name. Multi-tenant by design: `lagabelle-saint-hilaire.fr` users connect to `mail.zozoh.fr` and authenticate with their full email. |
| 4 | IMAPS:993 (implicit TLS) + submission 587 STARTTLS for both Thunderbird and Outlook | Consistent across clients. Submission 587 STARTTLS is what the role's `master.cf` already provides on every host; 465 is also enabled but 587 is the canonical submission port. |
| 5 | Publish RFC 6186 SRV records (`_imaps._tcp`, `_submission._tcp`, plus negative records for `_imap`, `_pop3`, `_pop3s`) | Modern clients skip HTTP discovery entirely. Cost is ~5 OVH records per domain via the existing `ovh_dns.py` loop. |
| 6 | Display strings derived from the domain; no `<documentation>` link | No webmail role exists. Zero per-domain YAML config needed to add a new domain. |
| 7 | nginx, not caddy | Aligns with the rest of the org's infra (legacy uses nginx; `proxy1` runs nginx). Caddy with its OVH DNS module would be more compact, but introducing a new runtime to one role is not worth the savings. |

## DNS records (extends `roles/mailserver/files/ovh_dns.py`)

Per domain in `mailserver_domains`, in addition to records already managed (A, MX, SPF, DKIM, DMARC, TLSA, `smtp`/`imap`/`autoconfig` CNAMEs):

| Name | Type | Value |
|------|------|-------|
| `autoconfig.<domain>` | CNAME | `mail.<primary_domain>.` (already created; idempotent) |
| `autodiscover.<domain>` | CNAME | `mail.<primary_domain>.` (new) |
| `_imaps._tcp.<domain>` | SRV | `0 1 993 mail.<primary_domain>.` |
| `_submission._tcp.<domain>` | SRV | `0 1 587 mail.<primary_domain>.` |
| `_imap._tcp.<domain>` | SRV | `0 0 .` (negative — no plaintext IMAP) |
| `_pop3._tcp.<domain>` | SRV | `0 0 .` (negative) |
| `_pop3s._tcp.<domain>` | SRV | `0 0 .` (negative) |

`<primary_domain>` is `mailserver_domain` from inventory (`zozoh.fr`).

## TLS — certbot DNS-01 via OVH

`roles/mailserver/tasks/certbot.yml` is refactored:

1. Install `certbot` and `python3-certbot-dns-ovh` (Debian package).
2. Render `/etc/letsencrypt/ovh.ini` (mode 0600, root:root) from existing `vault_ovh_application_key` / `vault_ovh_application_secret` / `vault_ovh_consumer_key`. The same credentials `ovh_dns.py` already uses.
3. For each domain `d` in `mailserver_domains`, issue:

   ```
   certbot certonly --non-interactive --agree-tos \
     -m {{ mailserver_admin_email }} \
     --dns-ovh --dns-ovh-credentials /etc/letsencrypt/ovh.ini \
     --dns-ovh-propagation-seconds 60 \
     -d "{{ d }}" -d "*.{{ d }}"
   ```

   This produces `/etc/letsencrypt/live/<d>/{fullchain,privkey}.pem`.

4. Install a deploy hook at `/etc/letsencrypt/renewal-hooks/deploy/zozoh-reload.sh` that reloads `nginx`, `postfix`, and `dovecot`.

### Postfix / Dovecot SNI

- **Dovecot** — extend `10-ssl.conf` with `local_name` blocks selecting per-domain certs. Default cert remains `mail.<primary_domain>`'s.
- **Postfix** — add `tls_server_sni_maps = hash:/etc/postfix/sni_map`, with one line per `<d> /etc/letsencrypt/live/<d>/privkey.pem /etc/letsencrypt/live/<d>/fullchain.pem` entry. `postmap -F` regenerates the lookup. Handler triggers `postfix reload`.

Mail clients connecting to `mail.zozoh.fr` get the `zozoh.fr` wildcard cert regardless of the user's email domain — this is correct because the advertised hostname in the XML and SRV records is always `mail.zozoh.fr`.

## Web server — nginx

A new task `roles/mailserver/tasks/autoconfig.yml`:

1. `apt install nginx`.
2. Disable `/etc/nginx/sites-enabled/default`.
3. Render `/etc/nginx/sites-available/mail-autoconfig.conf` from a template (see below), symlink into `sites-enabled/`.
4. Render the XML files into `/var/www/autoconfig/<sni-host>/...` in a loop over `mailserver_domains`.
5. `notify: reload nginx`.

### Vhost template (one file, all domains)

```nginx
map $host $autoconfig_cert {
{% for d in mailserver_domains %}
    ~*\.{{ d | regex_escape }}$  {{ d }};
{% endfor %}
    default {{ mailserver_domain }};
}

server {
    listen 80;
    listen [::]:80;
    server_name {{ autoconfig_server_names | join(' ') }};
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name {{ autoconfig_server_names | join(' ') }};

    ssl_certificate     /etc/letsencrypt/live/$autoconfig_cert/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$autoconfig_cert/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    root /var/www/autoconfig/$host;

    location = /mail/config-v1.1.xml         { default_type application/xml; }
    location = /autodiscover/autodiscover.xml { default_type application/xml; }
    # Outlook capitalization variants
    location ~* ^/Autodiscover/Autodiscover\.xml$ {
        rewrite ^ /autodiscover/autodiscover.xml last;
    }

    location / { return 404; }
}
```

`autoconfig_server_names` is computed in `roles/mailserver/vars/main.yml` (or via `set_fact`) as:

```yaml
autoconfig_server_names: "{{ mailserver_domains
    | map('regex_replace', '^(.+)$', 'autoconfig.\\1') | list
  + mailserver_domains
    | map('regex_replace', '^(.+)$', 'autodiscover.\\1') | list }}"
```

### XML templates

`roles/mailserver/templates/autoconfig/config-v1.1.xml.j2` (Thunderbird):

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

`roles/mailserver/templates/autoconfig/autodiscover.xml.j2` (Outlook POX, static):

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

Rendered with `with_items: "{{ mailserver_domains }}"` into both `autoconfig.<item>/mail/config-v1.1.xml` and `autodiscover.<item>/autodiscover/autodiscover.xml`.

## Variables

New entries in `roles/mailserver/defaults/main.yml`:

```yaml
mailserver_autoconfig_enabled: true

# Hostname advertised in the XML files and SRV records. Must be in the TLS cert.
# Defaults to mail.<primary_domain>, which matches the existing wildcard plan.
mailserver_autoconfig_advertised_host: "mail.{{ mailserver_domain }}"

# Certbot strategy: was 'standalone' (HTTP-01); now 'dns-ovh' (DNS-01).
mailserver_certbot_challenge: dns-ovh
mailserver_certbot_wildcards: true
mailserver_certbot_dns_propagation_seconds: 60
```

`mailserver_certbot_extra_domains` (currently `- mail.zozoh.fr`) becomes redundant once a wildcard issues — the role removes it from the default and keeps the variable optional for forward compatibility (empty list).

No new vault entries; existing `vault_ovh_application_key` / `_secret` / `_consumer_key` are reused.

## Task ordering inside the role

Reading `roles/mailserver/tasks/main.yml` / `tasks.yml`, the new order is:

1. `hostname.yml`
2. `packages.yml`  *(add `nginx`, `python3-certbot-dns-ovh`)*
3. `postgres.yml`
4. `vmail.yml`
5. `certbot.yml`  *(refactored to DNS-01; runs before any service that needs the cert)*
6. `postfix.yml` *(unchanged; reads cert paths)*
7. `dovecot.yml` *(SNI block added)*
8. `opendkim.yml`, `opendmarc.yml`, `spamassassin.yml`
9. `autoconfig.yml`  **(new — nginx vhost + XML)**
10. `dane_tlsa.yml`
11. `ovh_dns.yml`  *(extended for new CNAME + SRV records)*
12. `fail2ban.yml`, `backup.yml`, `monitoring.yml`, `services.yml`

`autoconfig.yml` is guarded by `when: mailserver_autoconfig_enabled`.

## Idempotency & handlers

- All file/template tasks use `notify: reload nginx`.
- Cert renewal triggers the deploy hook, not the role — handlers only fire on config drift.
- `ovh_dns.py` already de-duplicates records; new CNAME/SRV creation must be idempotent in the same way (lookup by `subDomain` + `fieldType`, update if `target` differs).

## Verification (run after deploy, before claiming done)

```sh
# DNS
dig +short autoconfig.zozoh.fr CNAME
dig +short autodiscover.zozoh.fr CNAME
dig +short _imaps._tcp.zozoh.fr SRV
dig +short _submission._tcp.zozoh.fr SRV

# TLS cert covers autoconfig & autodiscover SNI
echo | openssl s_client -servername autoconfig.zozoh.fr \
    -connect autoconfig.zozoh.fr:443 2>/dev/null \
  | openssl x509 -noout -subject -ext subjectAltName

# Thunderbird XML parses
curl -sS https://autoconfig.zozoh.fr/mail/config-v1.1.xml | xmllint --noout -

# Outlook XML parses (accepts GET on our static file)
curl -sS -X POST https://autodiscover.zozoh.fr/autodiscover/autodiscover.xml \
     -H 'Content-Type: text/xml' --data '<x/>' | xmllint --noout -

# Same set for lagabelle-saint-hilaire.fr
```

Real-client smoke: in Thunderbird, "Set up another account" → enter `test@zozoh.fr` → wizard must auto-fill both servers without manual editing. Repeat for `test@lagabelle-saint-hilaire.fr`.

Per the risky-ops pattern in memory, the first deploy runs against a Proxmox-snapshotted host with `--check --diff` before `--limit`-targeted real apply.

## Backward compatibility

`ovh_dns.py` already creates `imap.<domain>` and `smtp.<domain>` CNAMEs pointing at the role's hostname. We do **not** remove them — they keep working for clients with cached configuration, and per-domain wildcard certs cover them transparently. The new XML and SRV records simply stop *advertising* those names; new account setups land on `mail.<primary_domain>` instead.

## Non-goals

- Webmail (`<documentation>` link omitted; can be added later when a webmail role exists).
- POP3 advertising — IMAP-only.
- Microsoft autodiscover v2 (JSON / Outlook 365 mailbox setup). Current Outlook desktop with IMAP falls back to POX, which we cover.
- iOS `.mobileconfig` profiles — Apple Mail uses RFC 6186 SRV records which we provision, so wizard discovery still works.
- CalDAV / CardDAV SRV records — no calendar/contact server in scope.
- Firewall rules (UFW etc.) — the role does not currently manage host firewalls; out of scope here. Ports 80/443 are assumed reachable.

## Risks

- **Certbot refactor blast radius**: changing the ACME challenge type on a working host is the riskiest part. Mitigation: keep the old `--standalone` codepath behind `mailserver_certbot_challenge: standalone` for one cycle so the change is reversible by var. Verify the new path on a snapshotted host first.
- **OVH API rate limits**: DNS-01 issuance plus zone updates in one play burst can hit limits. Mitigation: `--dns-ovh-propagation-seconds 60` is sufficient in practice; serialize per-domain.
- **Wildcard cert + Postfix SNI**: existing `main.cf` references `mail.zozoh.fr` cert paths directly. After cutover the paths change to `/etc/letsencrypt/live/zozoh.fr/`. Mitigation: render via variable so the switch is a one-line var change, not a path rewrite per service.
