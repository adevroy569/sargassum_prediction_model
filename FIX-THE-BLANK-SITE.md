# Why the site shows no maps or charts

## What is actually wrong

The page itself is fine, and your `path: './site'` change was correct — Pages is
serving `site/` properly. `index.html`, `app.js` and `styles.css` all load.

What is missing is the **data**. On startup `app.js` fetches four files:

```
data/latest.json
data/forecast_segments.geojson
data/biomass_field.json
data/drift_tracks.json
```

which on the live site resolves to
`https://adevroy569.github.io/sargassum_prediction_model/data/latest.json`.
That URL returns **404**, because `site/data/` does not exist in the repo.

It does not exist because **the forecast job has never run.** The four pipeline
workflows are still sitting in `github-workflows/`. GitHub only runs workflows
from `.github/workflows/`, so `update.yml` has never fired, so nothing has ever
generated or committed `site/data/`.

Checked directly against the repo and the live site:

| URL | Result |
|---|---|
| `site/app.js` (raw) | 200 |
| `github-workflows/update.yml` (raw) | 200 |
| `.github/workflows/update.yml` (raw) | **404** |
| `site/data/latest.json` (raw) | **404** |
| `.../sargassum_prediction_model/data/latest.json` (live) | **404** |

To be sure the front end is not the problem, I installed the dependencies, ran
`scripts/update.py`, served `site/` locally with the generated `site/data/` in
place, and loaded it in a headless browser. Headline stats, all three charts and
the map layers render exactly as designed. The front end is not the issue.

---

## Step 1 — run the fix script (in the repo folder)

```powershell
cd C:\Users\Asus\Desktop\Claudeworkspace\sargassum_prediction_model
powershell -ExecutionPolicy Bypass -File .\fix-workflows.ps1
```

It moves `update.yml`, `backfill.yml`, `retrain.yml` and `test.yml` into
`.github/workflows/`, rewrites `static.yml` (see step 2), and deletes the
leftover `github-workflows/` folder.

I could not write those files directly: `.github/workflows/` is a protected path
over the desktop bridge, since files there run CI on your account. Everything
else in this fix I have written into the repo for you.

Then **commit and push in GitHub Desktop.**

## Step 2 — what changed in `static.yml`, and why it matters

Your deploy ran on `push` to `main` only. But the forecast job commits its
output using the built-in `GITHUB_TOKEN`, and **a push made with `GITHUB_TOKEN`
does not start other workflows.** So even once the data started being generated,
the Pages deploy would never re-run, and the site would sit frozen on whatever
you last pushed by hand.

Two changes:

- a `workflow_run` trigger on *Sargassum forecast update*, so a finished
  forecast redeploys the site;
- `ref: main` on the checkout — on a `workflow_run` event the default checkout
  ref is the commit that *started* the forecast job, which is one commit older
  than the data it just committed, so without this you would deploy the run
  before the new data every time.

Your `path: './site'` and the action versions are untouched.

## Step 3 — give Actions permission to write

**Settings → Actions → General → Workflow permissions → Read and write
permissions → Save.**

Without this the forecast job cannot commit `site/data/` back to the repo, and
it will fail on its push step.

## Step 4 — run the forecast once by hand

**Actions → Sargassum forecast update → Run workflow.**

It takes a few minutes — it downloads AFAI satellite reflectance from NOAA AOML
and current and wind fields from CariCOOS. When it finishes it commits
`site/data/`, that fires the Pages deploy, and the site fills in.

After this it is self-running: every 3 hours the forecast re-runs, commits, and
the site redeploys.

---

## If step 4 fails

Read the run log; it will name the step. The two likely causes:

- **`git push` denied** — step 3 was not done, or not saved.
- **`ErddapError: no AFAI product reachable`** — an upstream server was
  temporarily down. Transient; re-run the workflow. (This is also the one error
  I could not rule out from here: this sandbox blocks outbound traffic to
  `cwcgom.aoml.noaa.gov` and `dm3.caricoos.org`, so I could not fetch live data
  myself. GitHub's runners have open egress, so it should work there.)

## Worth knowing, once it is running

- **Schedule.** `update.yml` runs every 3 hours (`cron: '15 */3 * * *'`). The
  AFAI satellite composite only refreshes about once a day, so hourly would
  mostly re-process the same input. Change the cron if you want it anyway.
- **No trained model yet.** `data/models/` is empty, so every segment is served
  by physics plus the single global correction factor, and none will show as
  trap-trained. Run the **backfill** workflow once to build the training table
  and fit the model.
- **MapLibre comes from unpkg.com.** If the CDN is ever blocked or down, the map
  panel shows a message and the rest of the page still works. To make it
  bulletproof, vendor `maplibre-gl.js` and `maplibre-gl.css` into `site/` and
  point `index.html` at the local copies.

---

## Appendix — doing step 1 by hand

If PowerShell refuses to run the script, the same thing in File Explorer:

1. Create a folder `.github\workflows` in the repo (if `.github` already exists,
   just check `workflows` is inside it — it is).
2. Move `update.yml`, `backfill.yml`, `retrain.yml`, `test.yml` from
   `github-workflows\` into `.github\workflows\`.
3. Delete the empty `github-workflows\` folder.
4. Open `.github\workflows\static.yml` in a text editor and replace its whole
   contents with:

```yaml
# Simple workflow for deploying static content to GitHub Pages
name: Deploy static content to Pages

on:
  # Runs on pushes targeting the default branch
  push:
    branches: ["main"]

  # Runs after the forecast job finishes and commits new data.
  # This is not optional: a commit pushed by a workflow using the built-in
  # GITHUB_TOKEN does NOT start other workflows, so without this trigger the
  # site would keep serving whatever data you last pushed by hand.
  workflow_run:
    workflows: ["Sargassum forecast update"]
    types: [completed]

  # Allows you to run this workflow manually from the Actions tab
  workflow_dispatch:

# Sets permissions of the GITHUB_TOKEN to allow deployment to GitHub Pages
permissions:
  contents: read
  pages: write
  id-token: write

# Allow only one concurrent deployment, skipping runs queued between the run in-progress and latest queued.
# However, do NOT cancel in-progress runs as we want to allow these production deployments to complete.
concurrency:
  group: "pages"
  cancel-in-progress: false

jobs:
  # Single deploy job since we're just deploying
  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          # On a workflow_run event the default checkout ref is the commit that
          # STARTED the forecast job, which is one commit older than the data it
          # just committed. Always take the current tip of main.
          ref: main
      - name: Setup Pages
        uses: actions/configure-pages@v5
      - name: Upload artifact
        uses: actions/upload-pages-artifact@v3
        with:
          # Upload only the website folder
          path: './site'
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v5
```

Save it as UTF-8 **without** a byte order mark, or the Actions YAML parser will
reject it.

Then carry on from step 3 above.

---

`fix-workflows.ps1` and this file do not need to be in the repo. Uncheck them in
GitHub Desktop before committing if you would rather keep them out — the fix
works either way, since the script has already done its job by then.
