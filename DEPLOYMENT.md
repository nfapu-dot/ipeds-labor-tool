# Putting v2 on the web (Streamlit Community Cloud)

This mirrors how **v1** is hosted: a public GitHub repo with the data committed,
connected to a free **Streamlit Community Cloud** app. Once set up, every time you
push a change to GitHub the live app redeploys itself automatically.

You only do the one-time setup below once. After that, "update the website" = push to GitHub.

---

## What's already done (in this folder)

- Git repo initialized and everything committed locally — code **and** data.
- `.env` (your Census API key) is **excluded** from the repo. It will never be pushed.
- The app was hardened so it runs even before you add the Census key on the server
  (it reads the bundled Census data cache). See "About the Census key" below.

So the local folder is ready to push.

---

## Step 1 — Create the GitHub repo and push (you do this; needs your GitHub login)

You already have a GitHub account (`nfapu-dot`) connected to Streamlit Cloud for v1.
Create a **new** repo for v2 and push this folder to it.

**Option A — GitHub website (no extra tools):**
1. Go to <https://github.com/new>.
2. Name it e.g. `ipeds-labor-tool`. Set it to **Public**. Do **not** add a README/.gitignore
   (this folder already has them).
3. Click **Create repository**. GitHub shows a "push an existing repository" snippet.
4. Back here, run these two commands (replace the URL if you named it differently):

   ```
   git remote add origin https://github.com/nfapu-dot/ipeds-labor-tool.git
   git push -u origin main
   ```

   The first push uploads ~280 MB of data, so give it a minute. If git asks for a
   password, use a **GitHub personal access token** (Settings → Developer settings →
   Tokens), not your account password.

**Option B — `gh` CLI (if you install it):** `brew install gh && gh auth login`, then
`gh repo create nfapu-dot/ipeds-labor-tool --public --source=. --push`.

---

## Step 2 — Create the Streamlit app (you do this; same site as v1)

1. Go to <https://share.streamlit.io> and sign in with the same GitHub account.
2. Click **Create app** → **Deploy a public app from GitHub**.
3. Fill in:
   - **Repository:** `nfapu-dot/ipeds-labor-tool`
   - **Branch:** `main`
   - **Main file path:** `src/app_v2.py`   ← this is the important one (not `app.py`)
4. (Optional) **Advanced settings → Python version:** pick **3.11** or **3.12**.
   v1 runs fine on the default, so this is just for consistency.
5. Click **Deploy**. First build takes a few minutes while it installs packages and
   loads the IPEDS files. You'll get a public URL like
   `https://ipeds-labor-tool.streamlit.app`.

---

## Step 3 — (Recommended) Add the Census API key as a secret

The app already works without this, because the default Census query ships pre-cached
in the repo. Add the key only so that any *non-default* Census query also works.

1. In the app's menu (⋮) → **Settings → Secrets**.
2. Paste this one line (use your real key — free, instant signup at
   <https://api.census.gov/data/key_signup.html>):

   ```
   CENSUS_API_KEY = "your-key-here"
   ```

3. Save. The app reads it automatically (Streamlit exposes secrets as environment
   variables, which is what the code looks for).

**Never** put the key in the repo — only in this Secrets box.

---

## Updating the live app later

```
git add -A
git commit -m "describe your change"
git push
```

Streamlit Cloud notices the push and redeploys within a minute or two. No other steps.

---

## About the Census key (why the app survives without it)

`src/labor/loaders/census.py` saves every Census API response to
`data/raw_labor/census/*.json`, and the cache filename ignores the key. The default
state-level query's response is committed to the repo, so on a fresh server the app
reads that file and makes **zero** live API calls. The key is only needed if you change
the default Census query (different year/survey), which forces a fresh API call.

## Notes / limits of the free tier

- ~1 GB of memory. The app fits comfortably (v1 already runs the same large IPEDS files
  on this tier; v2's labor layer adds only ~100–150 MB on top).
- The server's disk is temporary — generated Excel workbooks aren't stored server-side.
  That's fine: the **Download** button streams the workbook straight to your computer.
- The app may "go to sleep" after a period of no use and take ~30 s to wake on the next
  visit. That's normal for the free tier.
