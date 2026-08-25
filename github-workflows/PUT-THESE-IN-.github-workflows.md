# Move these four files into `.github/workflows/`

The desktop bridge refuses to write into `.github/workflows/` — it is a
protected path, since files there run CI on your account. So the workflows
landed here instead.

Do this once, in the project folder:

```powershell
mkdir .github\workflows
move github-workflows\*.yml .github\workflows\
rmdir /s /q github-workflows
```

or on macOS/Linux:

```bash
mkdir -p .github/workflows && mv github-workflows/*.yml .github/workflows/ && rm -rf github-workflows
```

| File | What it does |
|---|---|
| `update.yml` | the scheduled forecast, every 3 hours |
| `backfill.yml` | manual: download AFAI history, build the training table, retrain |
| `retrain.yml` | weekly: extend the training table and refit |
| `test.yml` | on push: offline pipeline run + unit tests |

`update.yml`, `backfill.yml` and `retrain.yml` all commit back to the repo, so
under Settings → Actions → General, set **Workflow permissions** to
*Read and write permissions*.
