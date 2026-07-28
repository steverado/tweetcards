# tweetcards

Repurpose top-performing X posts into Instagram carousels for HumanPost.

Pipeline: **X analytics CSV → normalize → screenshot-style slides → host on GitHub/jsDelivr → queue to HumanPost.**

Slides are rendered to look like a real screenshot someone cropped — X's own native
palette, iPhone-class proportions, no card chrome — because screenshots outperform
obviously-designed cards.

## Accounts

| handle | display name | theme |
|---|---|---|
| techbromemes | Tech Bro Memes | dark (`#000000`) |
| LiteralMem3s | literal memes | dim (`#15202b`) |
| OldEldersonline | old people online | light (`#ffffff`) |

Edit `accounts.json` to tune display names, themes, `min_impressions` gates, the
`verified` badge, and avatar paths.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## Usage

**1. Normalize each account's X analytics export** (analytics.twitter.com → Export data).
Columns drift year to year, so check the mapping first:

```bash
python x_csv_to_posts.py techbromemes_export.csv --inspect
python x_csv_to_posts.py techbromemes_export.csv --account techbromemes
python x_csv_to_posts.py literalmemes_export.csv  --account LiteralMem3s   --append
python x_csv_to_posts.py oldelders_export.csv      --account OldEldersonline --append
```

Retweets and replies are dropped automatically. Output: `posts.json`.

**2. Render slides** — filters to the last 30 days, ranks by impressions per account,
chunks into carousels (2–10 slides), screenshots each at 1080×1350:

```bash
python render_cards.py                 # posts.json + accounts.json
python render_cards.py --posts posts.sample.json --min-per-carousel 1   # demo
```

Output: `out/<handle>/*.png` + `manifest.json` (slide order, post IDs, impressions,
source URLs, plus empty `caption` / `media_urls` to fill).

**3. Host the images** on GitHub + jsDelivr (needs a GitHub remote on this repo).
URLs are pinned to the commit SHA so they're immutable:

```bash
python publish_github.py --dry-run     # preview CDN URLs
python publish_github.py               # commit, push, write media_urls
```

**4. Queue to HumanPost** — via the MCP connector (see below). `create_post` takes a
carousel of 2–10 images; slide order = post order. Fill each carousel's `caption` in
`manifest.json` first.

## HumanPost MCP

The connector uses a **static Bearer token**, which the claude.ai web "Add custom
connector" flow can't attach (it forces an OAuth registration the server doesn't
support). Add it via the Claude Code CLI instead:

```bash
claude mcp add --transport http humanpost \
  https://us-central1-clickbaitcarousel.cloudfunctions.net/mcp \
  --header "Authorization: Bearer ccb_live_YOUR_KEY"
```

Key: create one at humanpost.co with scopes `accounts:read`, `posts:read`,
`posts:write`, `analytics:read`. Keep it out of git (it lives in the connector
config, not this repo).

## To-do before first real run

- [ ] Drop real profile images into `avatars/` (`techbromemes.png`, `LiteralMem3s.png`,
      `OldEldersonline.png`) — the initials circle is the one remaining fake tell.
- [ ] Export the three accounts' analytics CSVs and run step 1.
- [ ] Add a GitHub remote for step 3.
- [ ] Add the HumanPost connector and verify `list_accounts`.
