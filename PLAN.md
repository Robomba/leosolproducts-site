# Leo Sol Products Site (leosolproducts.com) — Project Plan

**Last updated:** 2026-07-09 13:20
**Status:** Active — BLOCKED on Ro's ~2-min Vercel GitHub App grant

## Current Status
Repo github.com/Robomba/leosolproducts-site is built (static: index.html Musicaguide landing, privacy, terms, vercel.json). Download buttons wired to GitHub Release latest URLs. Vercel (robomba's projects, Pro) needs its GitHub App installed on the repo — only Ro can click that popup. After that, import + deploy + domain (DNS already on Vercel).

## Goals
- leosolproducts.com live on Vercel serving the Musicaguide landing + downloads
- Becomes the distribution hub for all Léo Sol products (Musicaguide, Halo, future)

## Step-by-Step Next Actions
- [ ] Ro: vercel.com/new → "Adjust GitHub App Permissions" → grant Vercel access to leosolproducts-site → Install/Save [Ro]
- [ ] Import repo on Vercel: name leosolproducts-site, preset "Other", root ./ → Deploy [Cowork — Claude via browser]
- [ ] Project → Settings → Domains → add leosolproducts.com [Cowork — Claude via browser]
- [ ] Verify live site + both download buttons work [Cowork]
- [ ] Later: swap in signed Musicaguide .pkg; add Halo product page [Cowork]

## Decisions Made
| Date | Decision | Reason |
|------|----------|--------|
| 2026-06-29 | Static site on Vercel, downloads served from GitHub Releases | Simple, free, stable URLs |

## Blockers
| Blocker | Status |
|---------|--------|
| Vercel GitHub App not installed on repo (Ro-only popup) | Active |

## Cowork vs Claude Code — Project Overview
Cowork (browser leg) drives Vercel once Ro grants the app; Claude Code only if site code changes.

## Related Projects
| Project | Relationship | PLAN.md Path |
|---------|-------------|--------------|
| Musicaguide | Product this site distributes | /Volumes/commandcenter/projects/musicaguide-freemium-vst3/PLAN.md |
| Halo (by Léo Sol) | Future product page | /Volumes/commandcenter/projects/halo-by-leosol/PLAN.md |
