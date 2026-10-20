#!/usr/bin/env bash
# Every update: build the frontend here, copy the app to the server, and (re)run its setup there.
# Run deploy/launch.sh once first. Prints the link to send testers.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/.instance
export AWS_DEFAULT_REGION=$REGION
KEY=~/.ssh/mellow-ec2.pem
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new ubuntu@$IP"
RSYNC="rsync -az --delete -e 'ssh -i $KEY'"

# tldraw treats the server as production and needs a license key there (README: iPad).
grep -q '^VITE_TLDRAW_LICENSE_KEY=.' frontend/.env.local 2>/dev/null \
  || { echo "Put a tldraw key in frontend/.env.local first (see README)"; exit 1; }
# Codespaces change IP between sessions; re-allow ssh from this one (fails harmlessly when already allowed).
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 22 \
  --cidr "$(curl -s https://checkip.amazonaws.com)/32" >/dev/null 2>&1 || true

(cd frontend && npm run build)
until $SSH true 2>/dev/null; do echo "waiting for ssh..."; sleep 5; done
$SSH 'mkdir -p mellow; command -v rsync >/dev/null || (sudo apt-get update -q && sudo apt-get install -y -q rsync)'
# Excluded paths are left alone on the server: its .venv, database, results and Mathlib build stay.
eval $RSYNC --exclude .venv --exclude __pycache__ --exclude "'*.sqlite3*'" --exclude .env --exclude results \
  backend ubuntu@"$IP":mellow/
eval $RSYNC --exclude .lake lean ubuntu@"$IP":mellow/
eval $RSYNC frontend/dist/ ubuntu@"$IP":mellow/web/
eval $RSYNC deploy/server-setup.sh ubuntu@"$IP":mellow/
# The server's .env: this machine's keys, Lean in its default place (local disk there), and the access code.
{ grep -v -E '^(MELLOW_LEAN_DIR|ACCESS_CODE)=' backend/.env; echo "ACCESS_CODE=$ACCESS_CODE"; } \
  | $SSH 'umask 077; cat > mellow/backend/.env'
$SSH "HOST=$HOST bash mellow/server-setup.sh"
echo
echo "Send testers: https://$HOST/?key=$ACCESS_CODE"
echo "(open it in Safari, then Share > Add to Home Screen)"
