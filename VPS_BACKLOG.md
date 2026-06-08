# SaltStocks Backlog

---

## VPS Hosting Migration

> **Goal:** Move SaltStocks from local Mac app to Namecheap VPS so eBay OAuth works with a real HTTPS domain and the app is accessible from anywhere.
> **VPS purchased:** 2026-06-07

### Epic 1 — VPS Initial Setup
- [ ] Log into Namecheap VPS panel, note server IP address and root credentials
- [ ] SSH in as root: `ssh root@YOUR_IP`
- [ ] Change root password immediately
- [ ] Create a non-root sudo user (e.g. `holly`): `adduser holly && usermod -aG sudo holly`
- [ ] Copy your SSH key to the new user so you can log in without a password
- [ ] Disable root SSH login (`/etc/ssh/sshd_config` → `PermitRootLogin no`)
- [ ] `apt update && apt upgrade -y`
- [ ] Configure UFW firewall: allow SSH (22), HTTP (80), HTTPS (443), block everything else

### Epic 2 — Domain & DNS
- [ ] Decide which domain/subdomain to use (e.g. `saltstocks.yourdomain.com`)
- [ ] Add an **A record** in Namecheap DNS pointing that subdomain to your VPS IP
- [ ] Verify DNS propagation: `dig saltstocks.yourdomain.com` shows your IP

### Epic 3 — Install App Dependencies on Server
- [ ] Install Python 3.11+, git, pip: `apt install python3.11 python3.11-venv git -y`
- [ ] Clone the repo: `git clone <your-repo-url> ~/saltstocks`
- [ ] Create virtualenv and install requirements:
  ```
  python3.11 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  ```
- [ ] Verify app loads: `python -c "from app.main import app; print('OK')"`

### Epic 4 — nginx + HTTPS (Let's Encrypt)
- [ ] `apt install nginx certbot python3-certbot-nginx -y`
- [ ] Create nginx site config for your domain (reverse proxy to `127.0.0.1:8000`)
- [ ] Run `certbot --nginx -d saltstocks.yourdomain.com` — free SSL cert, auto-configures nginx
- [ ] Verify `https://saltstocks.yourdomain.com` is reachable (502 until app runs — expected)
- [ ] Confirm certbot auto-renewal: `systemctl status certbot.timer`

### Epic 5 — systemd Service (Keep App Running)
- [ ] Create `/etc/systemd/system/saltstocks.service` (runs uvicorn, restarts on crash, starts on reboot)
- [ ] `systemctl enable saltstocks && systemctl start saltstocks`
- [ ] Verify app is reachable at the HTTPS domain

### Epic 6 — Data Migration
- [ ] Transfer `data/saltstocks.db` from Mac to server via `scp`
- [ ] Decide whether to migrate `data/receipts/` or start fresh
- [ ] Run migrations on server to confirm schema is current
- [ ] Let `data/.session_secret` auto-generate on first start (or copy from Mac)

### Epic 7 — eBay OAuth
- [ ] In eBay developer portal → Production app → edit RuName
- [ ] Set redirect URI to `https://saltstocks.yourdomain.com/ebay/oauth/callback`
- [ ] Update RuName value in app Settings
- [ ] Click **Connect to eBay** — should complete with no copy-paste (fully automatic now)
- [ ] Do a dry-run import to confirm the full token → API → import flow works

### Epic 8 — Deploy Workflow
- [ ] Write `deploy.sh` on server: `git pull && pip install -r requirements.txt && systemctl restart saltstocks`
- [ ] Test a full round-trip: Mac code change → git push → `ssh holly@server ./deploy.sh` → live

### Epic 9 — Security Final Check
- [ ] Confirm login page is the first thing any browser sees
- [ ] Confirm only `/login`, `/setup`, `/static` are unauthenticated routes
- [ ] Install `fail2ban` to block brute-force SSH attempts
- [ ] Review nginx config: `server_tokens off`, no directory listing enabled
- [ ] Lock Cockpit (port 9090) to your home IP only:
  ```bash
  # Find your home IP first at https://whatismyip.com
  sudo firewall-cmd --permanent --remove-port=9090/tcp
  sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="YOUR_HOME_IP" port port="9090" protocol="tcp" accept'
  sudo firewall-cmd --reload
  ```
  Note: if your ISP changes your IP, SSH in and update the rule with your new IP.
- [ ] Add a real SSL cert to Cockpit to eliminate the "not private" browser warning (optional)

---

## Future / Icebox

- Email-based forgot-password flow (needs SMTP provider — revisit after VPS is live)
- PostgreSQL migration (only needed if multi-user or write volume grows — SQLite is fine for now)
- CI/CD pipeline (GitHub Actions → auto-deploy on push to main)
- Geekery Vault portfolio/demo site on same VPS
