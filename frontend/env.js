// Local-dev default. In Docker, docker-entrypoint.sh overwrites this file at
// container start from BACKEND_API_URL / FRONTEND_API_KEY env vars.
window.__API_BASE_URL__ = "http://localhost:8000";
window.__API_KEY__ = null;
