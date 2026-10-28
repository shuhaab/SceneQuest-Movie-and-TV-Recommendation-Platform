# Deploy SceneQuest on Vercel

## 1. Local preparation (already implemented)

- `pyproject.toml` points Vercel to `run:app`, the existing Flask application.
- `vercel.json` selects Flask, allows 30 seconds per invocation, and preserves static response headers.
- CSS, JavaScript, and images live in `public/static/`. Their URLs remain `/static/...`; Flask serves the same directory locally.
- `.vercelignore` excludes local credentials, the virtual environment, tests, and local artifacts from CLI uploads. `.gitignore` excludes `.env`, other local environment files, and `.vercel/`.

There is no frontend build command or output directory to enter. Vercel installs Python dependencies from `requirements.txt`. Local `.env` loading remains available; deployed credentials are supplied through Vercel's environment settings.

## 2. Push the prepared files

From the project directory, review and save the changes:

```sh
git status --short
git add .
git commit -m "Prepare SceneQuest for Vercel"
git push origin HEAD
```

The configured GitHub repository is `shuhaab/SceneQuest-Movie-and-TV-Recommendation-Platform`. The token file is ignored; never force-add it. If Git says there is nothing to commit, continue once the prepared files are visible on GitHub.

## 3. Import the GitHub repository

1. Sign in at https://vercel.com/new.
2. Choose **Import Git Repository**, connect GitHub if necessary, and import `SceneQuest-Movie-and-TV-Recommendation-Platform`.
3. Use the repository root as **Root Directory**. Select **Flask** if it is not detected automatically.
4. Keep build/install/output overrides disabled so Vercel uses its Flask defaults. Do not enter `flask run` as a build command.
5. Expand **Environment Variables** before deploying.

## 4. Add environment variables

| Name | Value | Environments |
|---|---|---|
| `TMDB_ACCESS_TOKEN` | Your actual TMDB API Read Access Token, without surrounding quote characters | Production and Preview |
| `SCENEQUEST_RATE_LIMITS` | `0` | Production and Preview |

`SCENEQUEST_RATE_LIMITS=0` disables the app's process-local IP limiter on Vercel; configure the firewall below for deployed traffic instead. This avoids treating instance-local counters or a hosting proxy address as a shared visitor limit. Do not add a `NEXT_PUBLIC_` prefix to the credential.

For an existing project, use **Settings → Environment Variables**. Changes apply to new deployments, so redeploy afterward. Paste the token directly into Vercel, not into chat or a source file committed to Git.

Select **Deploy**. Vercel will provide an HTTPS deployment URL.

## 5. Configure Vercel Firewall rate limiting

Open the project → **Firewall → Configure → New Rule**. These are Vercel-side counters shared by function instances; Vercel documents that counters are regional, not strict global counters.

### Hobby plan: one conservative demo rule

| Setting | Value |
|---|---|
| Condition | Request Path starts with `/api/v1/` |
| Action | Rate Limit |
| Strategy | Fixed Window |
| Key | IP |
| Window | 60 seconds |
| Limit | 10 requests |
| Exceeded action | Default 429 |

Save, then **Review Changes → Publish**. This applies the recommendation ceiling to all API requests because Hobby permits one rate-limiting rule. It is intentionally stricter for browsing than the original 60-request local limit: several rapid searches or refreshes can reach it. Wait for the next window if the UI reports too many requests.

### Plans with multiple rate-limiting rules

To retain the original separate targets, replace the demo rule with two non-overlapping rules:

- Path equals `/api/v1/recommendations`, POST: 10 requests per IP per 60 seconds.
- Path starts with `/api/v1/` AND does not equal `/api/v1/recommendations`: 60 requests per IP per 60 seconds.

Use Fixed Window and the Default 429 action for both. Do not select Log as the exceeded action; it does not block requests. Review any pricing shown in the dashboard before enabling a paid feature.

The existing public metadata cache remains per instance. This is safe for the demo because it stores reusable catalogue metadata; recommendation bodies/results are not cached, and every request still has its upstream attempt/deadline bounds. A shared database/cache is not required for correctness. A strict worldwide rate limit would require a shared counter service rather than these regional WAF rules.

## 6. Verify the deployment

1. Visit `/health`: expect `{"status":"ok"}`.
2. Open the homepage: CSS, navigation, and posters should load.
3. Search for `Arrival` and open a title to check details and regional providers.
4. Open **For you** and generate recommendations.
5. Check **About & credits** for the TMDB logo and JustWatch attribution.
6. If movies do not load, inspect **Logs**. `TOKEN_MISSING` means the token was not set for that deployment environment; `TOKEN_INVALID` means TMDB rejected its value. Correct it in Settings and redeploy.

Saved lists on the Vercel domain start separately from localhost because browser storage belongs to each origin. Keep the same production domain for stable browser records.

## References

- Flask deployment and static assets: https://vercel.com/docs/frameworks/backend/flask
- Environment variables: https://vercel.com/docs/environment-variables
- Firewall setup and plan limits: https://vercel.com/docs/vercel-firewall/vercel-waf/rate-limiting

The repository has been prepared and checked locally. An actual Vercel build, environment-variable setup, firewall configuration, and deployed smoke test still need to be completed in the user's account.
