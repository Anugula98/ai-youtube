# ai-youtube — Quick Setup & Local Frontend

## Repository

GitHub repository:

[ai-youtube repository](https://github.com/Anugula98/ai-youtube.git?utm_source=chatgpt.com)

## Quick Setup

If you have an existing local project, connect it to the repository and push the code:

```bash
git remote add origin https://github.com/Anugula98/ai-youtube.git
git branch -M main
git push -u origin main
```

If creating the repository from scratch:

```bash
echo "# ai-youtube" >> README.md
git init
git add README.md
git commit -m "first commit"
git branch -M main
git remote add origin https://github.com/Anugula98/ai-youtube.git
git push -u origin main
```

## Recommended Repository Files

The repository should include:

* `README.md`
* `LICENSE`
* `.gitignore`

## Frontend

The frontend can be opened directly in a browser:

```text
frontend/index.html
```

By default, the frontend calls the backend API at:

```text
http://localhost:8000/api/...
```

The API base URL is configured through:

```text
frontend/env.js
```

## Local Static Mount

A local static mount was added in:

```text
backend/app/main.py
```

The following frontend URLs are now available:

```text
http://127.0.0.1:8000/frontend/
http://127.0.0.1:8000/frontend/index.html
```

## Verification

The local server was verified with the following responses:

| Path                   | Status | Content Type       |
| ---------------------- | -----: | ------------------ |
| `/`                    |  `200` | `application/json` |
| `/frontend/`           |  `200` | `text/html`        |
| `/frontend/index.html` |  `200` | `text/html`        |

## Running Locally

Start the backend on port `8000`, then open:

```text
http://127.0.0.1:8000/frontend/
```

For environment-specific API configuration, keep values such as the API base URL in `frontend/env.js` or environment configuration rather than hard-coding them throughout the frontend.
