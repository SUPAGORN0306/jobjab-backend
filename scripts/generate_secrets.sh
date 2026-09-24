#!/usr/bin/env bash
# ============================================
# generate_secrets.sh — สร้าง secret keys
# ============================================
set -euo pipefail

command -v openssl >/dev/null 2>&1 || {
  echo "❌ ต้องติดตั้ง openssl ก่อน"
  exit 1
}

echo "🔐  JobJab — Secret Generator"
echo "════════════════════════════════════════════════════"
echo "JWT_SECRET_KEY=$(openssl rand -hex 32)"
echo "════════════════════════════════════════════════════"
echo "⚠️   คัดลอกไปใส่ .env"
echo "⚠️   อย่า commit ค่าพวกนี้!"
echo ""