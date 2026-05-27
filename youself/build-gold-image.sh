#!/bin/bash
# ============================================================
# build-gold-image.sh — Build youself.io gold image on Proxmox
# Run directly ON Proxmox host: bash /opt/build-gold-image.sh [TARGET_VMID]
# BASE_TEMPLATE is read from DB (system_settings) on each run - single source of truth.
# ============================================================
set -euo pipefail

HERMES_REPO="https://github.com/Hacksli/hermes-agent@main"
BUILDER_VMID=199
ADMIN_URL="https://api.youself.io"

# Backend repo on raw GitHub — we fetch the canonical setup-hermes.sh
# and scrub-vm.sh from here on every build so a push to that repo is
# enough to land in the next gold image (no manual scp to /var/lib/vz
# /snippets needed). Both files are intentionally kept in the backend
# repo because they encode platform conventions (wrappers, SOUL.md,
# token redaction) that belong with the server contract.
BACKEND_RAW="https://raw.githubusercontent.com/Hacksli/deployment-youself-io-go-backend/main/scripts"

# fetch_script <name> <output-path>
# Tries raw GitHub first; falls back to /var/lib/vz/snippets/<name> so a
# transient GitHub outage doesn't block a release-critical rebuild.
fetch_script() {
  local name="$1" out="$2"
  if wget -q "${BACKEND_RAW}/${name}" -O "$out" && [ -s "$out" ]; then
    log "  fetched ${name} from backend repo ($(wc -c < "$out") bytes)"
    return 0
  fi
  log "  WARNING: ${name} fetch from GitHub failed, falling back to /var/lib/vz/snippets/${name}"
  cp "/var/lib/vz/snippets/${name}" "$out"
}

# BASE_TEMPLATE — single source of truth is the DB
BASE_TEMPLATE=$(curl -s -H "Authorization: Bearer ${YOUSELF_ADMIN_TOKEN}" \
  "${ADMIN_URL}/admin/settings" | python3 -c \
  "import sys,json; s=json.load(sys.stdin); print(next((x['value'] for x in s if x['key']=='proxmox_template_vmid'), '9042'))" \
  2>/dev/null || echo '9042')

TARGET_VMID=${1:-$((BASE_TEMPLATE + 1))}
VERSION=$(curl -s "https://raw.githubusercontent.com/Hacksli/hermes-agent/main/hermes_cli/__version__.py" \
  2>/dev/null | grep -o '"[0-9.]*"' | tr -d '"' || echo "latest")

log() { echo "[$(date '+%H:%M:%S')] $*"; }

guest_exec() {
  # Run command synchronously in VM with extended timeout
  local cmd="$1" timeout_sec="${2:-120}"
  log "  Executing (timeout ${timeout_sec}s): ${cmd:0:70}..."
  local result
  result=$(qm guest exec "$BUILDER_VMID" --timeout "$timeout_sec" -- sh -c "$cmd" 2>/dev/null)
  local out exit_code
  out=$(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('out-data','').strip())" 2>/dev/null)
  exit_code=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('exitcode',0))" 2>/dev/null || echo 0)
  [ -n "$out" ] && echo "$out"
  [ "$exit_code" != "0" ] && log "  WARNING: exit $exit_code"
  return 0
}

# Keep guest_long as alias for compatibility
guest_long() { guest_exec "$1" "${2:-120}"; }

log "=== Building gold image v${VERSION} (VMID ${TARGET_VMID}) from ${BASE_TEMPLATE} ==="

# 1. Clone base template → builder VM
log "Step 1: Cloning ${BASE_TEMPLATE} → builder ${BUILDER_VMID}"
qm stop ${BUILDER_VMID} --timeout 10 2>/dev/null || true; sleep 3
qm destroy ${BUILDER_VMID} --purge 2>/dev/null || true
sleep 2
rm -rf /var/lib/vz/images/${BUILDER_VMID}/ 2>/dev/null || true
rm -f /etc/pve/qemu-server/${BUILDER_VMID}.conf 2>/dev/null || true
qm clone ${BASE_TEMPLATE} ${BUILDER_VMID} --name hermes-gold-builder --full 1
qm set ${BUILDER_VMID} --cpu host --ipconfig0 ip=dhcp --nameserver 8.8.8.8
qm start ${BUILDER_VMID}

# 2. Wait for guest agent - ensure it can actually execute commands
log "Step 2: Waiting for VM to boot..."
for i in $(seq 1 60); do
  OUT=$(qm guest exec ${BUILDER_VMID} -- sh -c 'echo vm_ready' 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('out-data','').strip())" 2>/dev/null || echo '')
  if [ "$OUT" = "vm_ready" ]; then
    log "VM ready after $((i*3))s"
    break
  fi
  sleep 3
done

# 3. Install latest hermes and patch youself identity
log "Step 3: Installing latest hermes"
guest_long "pip3 install --no-cache-dir --break-system-packages --quiet --force-reinstall git+${HERMES_REPO} && echo hermes_ok" 180

# 3b. Download and apply our custom main.py directly (pip may install PyPI version)
log "Step 3b: Patching hermes_cli/main.py with youself identity"
guest_long "wget -q 'https://raw.githubusercontent.com/Hacksli/hermes-agent/main/hermes_cli/main.py' -O /usr/lib/python3.12/site-packages/hermes_cli/main.py && echo patch_ok" 60

# 4. Apply setup-hermes.sh (fetched fresh from backend repo each build)
log "Step 4: Applying hermes setup"
SETUP_PATH=$(mktemp)
fetch_script setup-hermes.sh "$SETUP_PATH"
SETUP_B64=$(base64 -w0 "$SETUP_PATH")
rm -f "$SETUP_PATH"
guest_long "echo '${SETUP_B64}' | base64 -d > /tmp/s.sh && sh /tmp/s.sh" 60

# 5. Verify AIAgent identity via guest_long
log "Step 5: Verifying hermes AIAgent identity"
guest_long "grep -l 'youself_identity' /usr/lib/python*/site-packages/hermes_cli/main.py 2>/dev/null && echo AIAgent_OK || echo AIAgent_MISSING" 30
log "  (verification complete)"

# 6. Scrub (fetched fresh from backend repo each build)
log "Step 6: Scrubbing VM"
SCRUB_PATH=$(mktemp)
fetch_script scrub-vm.sh "$SCRUB_PATH"
SCRUB_B64=$(base64 -w0 "$SCRUB_PATH")
rm -f "$SCRUB_PATH"
guest_long "echo '${SCRUB_B64}' | base64 -d > /tmp/sc.sh && sh /tmp/sc.sh" 30

# 7. Shutdown
log "Step 7: Shutting down builder"
qm shutdown ${BUILDER_VMID} --timeout 30
sleep 10

# 8. Create template
log "Step 8: Creating template VMID ${TARGET_VMID}"
qm destroy ${TARGET_VMID} --purge 2>/dev/null || true
sleep 2
qm clone ${BUILDER_VMID} ${TARGET_VMID} --name "youself-gold-v${VERSION}" --full 1
qm template ${TARGET_VMID}
qm set ${TARGET_VMID} --description "hermes ${VERSION}; AIAgent mode; built $(date +%Y-%m-%d)"
qm destroy ${BUILDER_VMID} --purge

log "Step 9: BASE_TEMPLATE is read from DB on each run - no local update needed"

# 10. Update system_settings via Admin API
log "Step 10: Updating proxmox_template_vmid in DB"
RESP=$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
  -H "Authorization: Bearer ${YOUSELF_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"value\":\"${TARGET_VMID}\"}" \
  "https://api.youself.io/admin/settings/proxmox_template_vmid")
if [ "$RESP" = "200" ]; then
  log "DB updated: proxmox_template_vmid=${TARGET_VMID}"
else
  log "WARNING: DB update failed (HTTP $RESP)"
fi

log "=== ✅ Gold image v${VERSION} ready at VMID ${TARGET_VMID} ==="
