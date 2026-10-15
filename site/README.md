# HydroWitness website

A static site, six pages: Home, How it works, Technology, Demo, Team and Contact us. No build step to host it, no dependencies, no tracking, no requests to other sites.

```
index.html  how-it-works.html  technology.html  demo.html  team.html  contact.html
assets/     site.css, site.js, replay-logic.js, replay.js, demo.js, fonts/, img/, vendor/ (the scroll library)
demo/       the real dashboard running from recordings (app.html, shim.js, index.json, rec/*.json.gz)
vercel.json and _headers: the same security headers, for Vercel and for Netlify
```

The six `.html` pages are **generated** from `../tools/site_src/` by `../tools/build_site.py` so the menu and footer are written once. To change the words on a page, edit its file in `tools/site_src/`, then run `python tools/build_site.py` from the main folder. (`python -m pytest tests/test_site.py` fails if the pages and the sources disagree.)

## See it on your computer

From this folder, run `python -m http.server 8000` (on Windows: `py -m http.server 8000`) and open http://localhost:8000.

## Publish it on Vercel or Netlify (either works)

### Vercel

1. Make sure the project is on GitHub (the whole `hydrowitness` repository, with this `site` folder in it). The repository can stay private.
2. On vercel.com, sign in with GitHub, choose **Add New... then Project**, and import the `hydrowitness` repository. If it is private, allow Vercel to see it when it asks.
3. Set **Root Directory** to `site`. Set **Framework Preset** to **Other**. Leave the build command and output directory empty.
4. Click **Deploy**. Every later push to GitHub updates the site by itself.

### Netlify

1. Make sure the repository is on GitHub (it can stay private).
2. On netlify.com, sign in with GitHub, choose **Add new project, then Import an existing project**, pick GitHub, and choose the `hydrowitness` repository. If it is private, allow Netlify to see just that repository.
3. Leave **Base directory** empty and the **Build command** empty. Set **Publish directory** to `site`.
4. Click **Deploy**. Every later push to GitHub updates the site by itself. Netlify reads the headers from `site/_headers`.

## Before you publish

- **Email:** the public contact address is set in `tools/build_site.py` (the line starting `EMAIL =`). To change it, edit that line and run `python tools/build_site.py`. It appears on the Contact page only.
- **Logo:** the team's logo is in `assets/img/`: `logo-mark.png` (the square mark in the menu), `logo-full.webp` (the footer and the Team page) and `favicon-64.png` (the browser tab). To change it, replace those files, keeping the names.
- **Names:** the Team page lists roles only. Add names in `tools/site_src/team.html` if everyone agrees to be listed.
- **Read every claim once.** Each number comes from a saved result (`results/`), and `tests/test_site.py` checks that they still match, but a person should check the words.

## The demo

`demo/app.html` is the real dashboard page. `demo/shim.js` answers its `/api/...` calls from recordings of the real engines running each drill on the simulated plant (`demo/rec/`). It needs no server. Anything a recording can't answer (breaking things yourself, typing in your own readings) gets a plain explanation instead.

If the engines, the drills or the dashboard change, regenerate the recordings: `python -m hydrowitness.sim.web_recordings`, then `python -m pytest tests/test_web_demo.py`. A failing test there means the demo and the software disagree.

## Keep the numbers true

If the drills or tests change, re-run `python -m hydrowitness.sim.validation_report`, then `python tools/build_site.py` (it rebuilds the results table from `results/v2_drills.json`), then `python -m pytest tests/test_site.py`. Also update the test count on the Technology page and in the main README (a test checks they match).

## Security headers

`vercel.json` (Vercel) and `_headers` (Netlify) set a strict Content-Security-Policy (everything from the site itself; no other origins), `X-Frame-Options: SAMEORIGIN` (the Demo page frames the dashboard) and a short cache time for `assets/`.

Fonts: Manrope and IBM Plex Mono, under the SIL Open Font License (licenses are in `assets/fonts/`).

Smooth scrolling: [Lenis](https://github.com/darkroomengineering/lenis) (MIT licence), kept in `assets/vendor/` with its licence, gives mouse wheels and trackpads a softer, inertial scroll. Phones keep their own native scrolling, and it is switched off for anyone whose system asks for reduced motion. If the file is ever missing, the pages simply scroll normally.
