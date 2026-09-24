#!/usr/bin/env bash
# ============================================
# setup_foundation.sh — ติดตั้ง Phase 1.1
# ============================================
set -euo pipefail

cd "$(dirname "$0")/.."

echo "📦  Phase 1.1 — Foundation Setup"
echo "════════════════════════════════════"

# 1. สร้างโฟลเดอร์
mkdir -p scripts docs
echo "✅  สร้าง scripts/ และ docs/"

# 2. .env.example
cat > .env.example << 'ENVEOF'
# ============================================
# JobJab Backend — Environment Variables
# ============================================
ENV=development
FLASK_APP=app.py
FLASK_DEBUG=1

DATABASE_URL=postgresql://postgres.xxx:yyy@aws-0-ap-xxx.pooler.supabase.com:6543/postgres?pgbouncer=true

SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_KEY=eyJhbGc...

JWT_SECRET_KEY=CHANGE_ME
JWT_REFRESH_SECRET_KEY=CHANGE_ME
JWT_ACCESS_TOKEN_EXPIRES_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRES_DAYS=7

CSRF_SECRET_KEY=CHANGE_ME
CSRF_TOKEN_EXPIRES_HOURS=24

REDIS_URL=memory://

CORS_ORIGINS=http://localhost:5173,https://jobjab-one.vercel.app

COOKIE_DOMAIN=
COOKIE_SECURE=0

RATELIMIT_STORAGE_URI=
RATELIMIT_ENABLED=1

LOG_LEVEL=INFO
LOG_FORMAT=console

SENTRY_DSN=
ENVEOF
echo "✅  สร้าง .env.example"

# 3. generate_secrets.sh
cat > scripts/generate_secrets.sh << 'GENEOF'
#!/usr/bin/env bash
set -euo pipefail
command -v openssl >/dev/null 2>&1 || { echo "❌ ต้องติดตั้ง openssl"; exit 1; }
echo ""
echo "🔐  JobJab — Secret Generator"
echo "════════════════════════════════════════════════════"
echo ""
echo "JWT_SECRET_KEY=$(openssl rand -hex 32)"
echo ""
echo "JWT_REFRESH_SECRET_KEY=$(openssl rand -hex 32)"
echo ""
echo "CSRF_SECRET_KEY=$(openssl rand -hex 32)"
echo ""
echo "════════════════════════════════════════════════════"
echo ""
GENEOF
chmod +x scripts/generate_secrets.sh
echo "✅  สร้าง scripts/generate_secrets.sh"

echo ""
echo "════════════════════════════════════════"
echo "🎉  Phase 1.1 เสร็จ!"
echo ""
echo "ขั้นตอนต่อไป:"
echo "  1.  cp .env.example .env"
echo "  2.  ./scripts/generate_secrets.sh"
echo "  3.  เติมค่าจริงใน .env (DATABASE_URL, SUPABASE_*)"
echo "  4.  cp ค่า secret จากข้อ 2 → .env"
echo ""