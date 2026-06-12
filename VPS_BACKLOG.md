# SaltStocks Backlog

---

## VPS Hosting Migration

> **Goal:** Move SaltStocks from local Mac app to Namecheap VPS so eBay OAuth works with a real HTTPS domain and the app is accessible from anywhere.
> **VPS purchased:** 2026-06-07

### Epic 1 — VPS Initial Setup ✅
- [x] Log into Namecheap VPS panel, note server IP address and root credentials
- [x] SSH in as root: `ssh root@YOUR_IP`
- [x] Change root password immediately
- [x] Create a non-root sudo user: `adduser holly && usermod -aG wheel holly` (CentOS: wheel not sudo)
- [x] Copy your SSH key to the new user so you can log in without a password
- [x] Disable root SSH login (`/etc/ssh/sshd_config` → `PermitRootLogin no`)
- [x] `yum update -y` (CentOS — not apt)
- [x] Configure firewalld: allow SSH (22), HTTP (80), HTTPS (443), block everything else

### Epic 2 — Domain & DNS ✅
- [x] Decided on `saltstocks.geekeryvault.co`
- [x] Added A record in Namecheap DNS pointing to 162.0.222.94
- [x] Verified DNS propagation: `dig saltstocks.geekeryvault.co` shows 162.0.222.94

### Epic 3 — Install App Dependencies on Server ✅
- [x] Installed Python 3.11, git: `yum install python3.11 python3.11-devel git -y`
- [x] Cloned repo via SSH: `git clone git@github.com:hyfeagin/saltstocks.git ~/saltstocks`
- [x] Created virtualenv and installed requirements (excluding pyobjc — macOS only)
- [x] Verified app loads: `python -c "from app.main import app; print('OK')"`

### Epic 4 — nginx + HTTPS (Let's Encrypt) ✅
- [x] Installed nginx, certbot, python3-certbot-nginx
- [x] Created nginx site config at `/etc/nginx/conf.d/saltstocks.conf`
- [x] Ran `certbot --nginx -d saltstocks.geekeryvault.co` — cert issued, auto-renewal configured
- [x] `https://saltstocks.geekeryvault.co` is reachable

### Epic 5 — systemd Service (Keep App Running) ✅
- [x] Created `/etc/systemd/system/saltstocks.service`
- [x] `systemctl enable --now saltstocks`
- [x] App is reachable at https://saltstocks.geekeryvault.co

### Epic 6 — Data Migration ✅
- [x] Transferred `data/saltstocks.db` from Mac to server via `scp`
- [x] Fixed bcrypt version conflict (pinned to 3.2.2 — bcrypt 5.x incompatible with passlib 1.7.4)
- [x] Login confirmed working with migrated data
- [ ] Decide whether to migrate `data/receipts/` or start fresh

### Epic 7 — eBay OAuth ✅
- [x] Updated redirect URI to `https://saltstocks.geekeryvault.co/ebay/oauth/callback`
- [x] Update RuName value in app Settings
- [x] Click **Connect to eBay** — should complete with no copy-paste (fully automatic now)
- [x] Do a dry-run import to confirm the full token → API → import flow works

### Epic 8 — Deploy Workflow ✅
- [x] Created `~/deploy.sh` on server: git pull + systemctl restart (no pip — fast deploys)
- [x] Nightly cron job at 2am syncs `requirements.txt` to server venv (`~/sync-deps.sh`)
- [x] Created `shortcuts/Deploy to Server.command` — double-click to deploy from Mac
- [x] Created `shortcuts/Pull Production DB.command` — double-click to pull prod DB to local
- [x] Created `shortcuts/Push DB to Server.command` — one-time DB migration with confirmation

### Epic 9 — Security Final Check ✅
- [x] Confirmed login page is the first thing any browser sees
- [x] Confirmed only `/login`, `/setup`, `/static` are unauthenticated routes
- [x] Installed fail2ban to block brute-force SSH attempts
- [x] nginx config: `server_tokens off` added
- [ ] Lock Cockpit (port 9090) to your home IP only (revisit when needed):
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
