#!/bin/sh
set -e

# Regenerate env.js from environment variables so the same built image can be
# pointed at any backend URL/key without rebuilding.
if [ -n "$FRONTEND_API_KEY" ]; then
  API_KEY_JS="\"$FRONTEND_API_KEY\""
else
  API_KEY_JS="null"
fi

cat > /usr/share/nginx/html/env.js <<EOF
window.__API_BASE_URL__ = "${BACKEND_API_URL:-http://localhost:8000}";
window.__API_KEY__ = ${API_KEY_JS};
EOF

exec nginx -g "daemon off;"
