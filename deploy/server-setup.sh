#!/usr/bin/env bash
# Runs on the server (deploy.sh calls it): installs what's missing and restarts the app. Safe to run again.
# Caddy serves the built frontend and forwards /api to FastAPI, with HTTPS from Let's Encrypt for HOST
# (an sslip.io name, which resolves to the IP inside it, so no domain is needed). The page needs HTTPS: it uses
# crypto.randomUUID, which browsers only allow on secure pages.
set -euo pipefail
: "${HOST:?set HOST to the sslip.io name of this server}"
cd ~/mellow

if ! command -v caddy >/dev/null || ! command -v bwrap >/dev/null; then
  sudo apt-get update -q
  sudo apt-get install -y -q bubblewrap caddy git curl
fi

# Ubuntu 24.04 blocks the user namespaces Lean's sandbox (bwrap) needs, unless an AppArmor profile allows them.
probe=(bwrap --unshare-all --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/lib64 /lib64 /usr/bin/true)
if ! "${probe[@]}" 2>/dev/null; then
  sudo tee /etc/apparmor.d/bwrap >/dev/null <<'EOF'
abi <abi/4.0>,
include <tunables/global>
profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
}
EOF
  sudo apparmor_parser -r /etc/apparmor.d/bwrap
  "${probe[@]}" || { echo "bwrap still can't sandbox; Lean must not run unsandboxed"; exit 1; }
fi

# Lean + Mathlib: elan installs the project's toolchain; the Mathlib cache is prebuilt (a few minutes the first time).
[ -x ~/.elan/bin/elan ] || curl -sSf https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
  | sh -s -- -y --default-toolchain none
(cd lean && PATH=~/.elan/bin:$PATH lake exe cache get)

# Python: uv brings the same version the app is developed on (Ubuntu's own is older).
[ -x ~/.local/bin/uv ] || curl -LsSf https://astral.sh/uv/install.sh | sh
~/.local/bin/uv venv -q --allow-existing --python 3.14 backend/.venv
~/.local/bin/uv pip install -q --python backend/.venv/bin/python -r backend/requirements.txt

sudo tee /etc/systemd/system/mellow.service >/dev/null <<EOF
[Unit]
Description=Mellow backend
After=network.target

[Service]
User=$USER
WorkingDirectory=$HOME/mellow/backend
ExecStart=$HOME/mellow/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# Caddy runs as its own user, which can't read ~ on Ubuntu, so the built frontend is served from /srv.
sudo mkdir -p /srv/mellow
sudo rsync -a --delete web/ /srv/mellow/
sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
$HOST {
	encode gzip
	handle_path /api/* {
		reverse_proxy 127.0.0.1:8000
	}
	handle {
		root * /srv/mellow
		try_files {path} /index.html
		file_server
	}
}
EOF

sudo systemctl daemon-reload
sudo systemctl enable -q mellow caddy
sudo systemctl restart mellow
sudo systemctl reload-or-restart caddy
# Read Mathlib once so the first student check doesn't wait on the disk (cold: ~50 s, warm: ~5 s).
(find lean/.lake -name '*.olean' -print0 | xargs -0 cat) >/dev/null 2>&1 &

for _ in $(seq 20); do curl -sf localhost:8000/health && echo && exit 0; sleep 1; done
echo "backend didn't start: journalctl -u mellow -n 50"; exit 1
