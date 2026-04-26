# Deploying the web validator to `validate.the-dwc.com`

Step-by-step runbook for the one-time Cloudflare Pages + DNS setup that takes the validator from "builds locally" to "live at https://validate.the-dwc.com/". Pre-flight only — once these are done, every push to `main` ships automatically via [`.github/workflows/web-validator.yml`](../../.github/workflows/web-validator.yml).

## Pre-flight checklist

- [ ] Cloudflare account exists and `the-dwc.com` is on it (the apex zone is already a Cloudflare site, since `ns.the-dwc.com` is hosted there).
- [ ] Cloudflare API token created with permissions `Account:Cloudflare Pages:Edit` and `Zone:DNS:Edit` for the `the-dwc.com` zone (dash → User → API Tokens → Create Token → Custom token).
- [ ] Cloudflare Account ID copied from the dashboard right sidebar.
- [ ] `wrangler` CLI optional but useful for local sanity checks: `npm i -g wrangler`.

## One-time setup

### 1. Create the Pages project

Easiest via the dashboard:

1. Cloudflare dash → Workers & Pages → Create application → Pages → Direct Upload.
2. Name the project **`dwc-validator`** exactly. The GitHub Actions workflow targets that name (`--project-name=dwc-validator`).
3. Production branch: `main`.
4. Click Create — the project is empty until first deploy.

(Or via CLI: `wrangler pages project create dwc-validator --production-branch=main`.)

### 2. Set the GitHub repo secrets

Repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret name             | Value                                              |
|-------------------------|----------------------------------------------------|
| `CLOUDFLARE_API_TOKEN`  | The token from pre-flight                          |
| `CLOUDFLARE_ACCOUNT_ID` | The account ID from pre-flight                     |

The workflow already gates on these — it deploys only when both are present. Until they're set, every push produces a `web-validator-dist` artifact in Actions but skips the Pages upload.

### 3. Trigger the first deploy

Two paths:

- **Push something to `main`** that touches `tools/web-validator/**` or `src/dwc_sidecar/**`. The workflow runs `build` then `deploy`, lands the bundle at the auto-assigned `https://dwc-validator.pages.dev`.
- **Manually** via Actions → web-validator → Run workflow → main.

Wait for it to go green. Visit `https://dwc-validator.pages.dev` to confirm the validator loads, picks up the bundled wheel via `manifest.json`, and parses a stub sidecar.

### 4. Attach the custom domain

In the dashboard:

1. Pages → dwc-validator → Custom domains → Set up a custom domain.
2. Enter **`validate.the-dwc.com`**.
3. Cloudflare offers to add the DNS record automatically since `the-dwc.com` is on the same account — accept. This creates a `CNAME` for `validate` pointing at `dwc-validator.pages.dev` (with proxy enabled).
4. Wait for SSL provisioning (~1–5 minutes — Cloudflare issues a Universal SSL cert covering `validate.the-dwc.com`).

(Or via CLI: `wrangler pages project add-domain dwc-validator validate.the-dwc.com`.)

### 5. Verify

```bash
# DNS resolves to a Cloudflare edge IP
dig +short validate.the-dwc.com

# TLS works
curl -sI https://validate.the-dwc.com/ | head -3
# → HTTP/2 200, server: cloudflare

# Cache headers per `_headers`
curl -sI https://validate.the-dwc.com/dwc_sidecar-0.3.0-py3-none-any.whl | grep -i cache-control
# → cache-control: public, max-age=31536000, immutable

curl -sI https://validate.the-dwc.com/index.html | grep -i cache-control
# → cache-control: public, max-age=300
```

If the second curl returns `max-age=300` despite `_headers` requesting longer for the wheel, Cloudflare Pages may be enforcing a tier-wide clamp (the `dwc-schemas` project has shown this behaviour — see `~/.claude/.../memory/project_cache_control_limitation.md`). Rare, but not impossible on the Free tier; upgrade to Pro if it bites.

## Subsequent releases

After the one-time setup, deploys are fully automated. Every push to `main` that touches the validator or the package re-builds the dist and deploys. There's no manual step.

To force a fresh deploy after a Cloudflare-side issue: Actions → web-validator → Run workflow → main. The build is deterministic; you'll get the exact same bundle.

## When it fails

The workflow's `deploy` job uses `cloudflare/wrangler-action@v3` under the hood. Common failure modes:

- **`Project not found: dwc-validator`** → step 1 wasn't completed, or the project name in the workflow (`--project-name=dwc-validator`) was edited to something different. Confirm the project exists at the dashboard and the names match.
- **`Authentication error: insufficient scope`** → the API token from pre-flight is missing the `Cloudflare Pages:Edit` permission. Generate a fresh token with both required permissions.
- **Build succeeds but the deploy step is skipped (`Skipping Cloudflare Pages deploy — secrets not set`)** → the GitHub secrets aren't visible to the workflow, usually because they were added at the Environment level instead of the Repository level. Move them to Repository.
- **DNS resolves but HTTPS fails with "site can't be reached"** → SSL cert is still provisioning; wait 5 minutes and retry.
- **Validator loads but `pyodide.loadPyodide()` 404s** → Pyodide CDN URL changed; check `manifest.json`'s `pyodide_version` against the current `https://cdn.jsdelivr.net/pyodide/v<version>/full/pyodide.js`.

## Migrating later

`validate.the-dwc.com` is intentionally a separate subdomain (plan §8 Q3, resolved 2026-04-26 in favour of subdomain over path). Repointing it to a different host (e.g. a Cloudflare Worker, or off-Cloudflare entirely) is a one-line CNAME change at the apex zone. URLs published to users — `https://validate.the-dwc.com/` and the documented sidecar drop-zone — never break.

## Related

- [`.github/workflows/web-validator.yml`](../../.github/workflows/web-validator.yml) — the build + deploy pipeline.
- [`build.py`](build.py) — the dist/ generator.
- [`_headers`](_headers) — Cloudflare Pages cache rules.
- Plan §4 — design context for the in-browser validator.
