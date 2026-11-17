# Cursor and GitHub Copilot quotas

> **Status**: informational, perishable. Cursor and GitHub Copilot plans, prices and quotas as read on their pages on 2026-11-09 and 2026-11-10; no repository fact, nothing to verify in it. Re-read the source pages before relying on it. · **Last verified**: 2026-11-12 (internal consistency and links only; prices were not re-verified)

**Verdict:** as of 2026-11-10, both products bill paid usage beyond an included plan, but not in the same unit. The Cursor sold to new customers is a tier of The X Subscription: a weekly pool of Cursor models, with no size stated in tokens, and third-party models at API price. GitHub Copilot measures current usage in GitHub AI Credits, with 1 credit = 0.01 USD, computed from tokens. The older request-based counts still exist, each on its own page, and neither gives a rate that converts a Copilot premium request into credits, nor a Cursor token into a Copilot token.

Research note of 2026-11-10.

The pages were read that day. The HTTP header of `https://cursor.com/pricing` carries `Date: Tue, 10 Nov 2026`. The Copilot marketing page, in its Contentful JSON, carries `updatedAt` `2026-11-09T13:28:05.602Z`. Neither of these pages shows a price in EUR. This note does not describe the consumption of an account.

## Question

For each individual or team plan still named by Cursor and by GitHub Copilot: what is the monthly price, what is included (requests, credits, tokens, agent, multipliers), what happens when the plan is exhausted, and are "credit" and "token" the same unit?

## Short answer

The names Hobby, Pro and Pro+ are no longer the plans sold to new Cursor customers. The page [Legacy Cursor plans](https://cursor.com/docs/account/pricing/legacy-cursor-plans) reserves them for accounts already subscribed. On the Copilot side, Pro and Pro+ are on sale; there is no Ultra plan. The highest individual tier is called Copilot Max.

### Cursor, plans on sale

Monthly prices on [cursor.com/pricing](https://cursor.com/pricing). The content of the pool is on [Models & Pricing](https://cursor.com/docs/models-and-pricing), which is the target of the 308 from `https://cursor.com/docs/account/pricing`.

| Plan | Price | Included pool | Beyond |
| --- | --- | --- | --- |
| Free | 0 USD / month | "Limited": limited agent and chat, no number | The page describes no purchase. Third-party models are not available. |
| Plus | 8 USD / month, or 80 USD / year | "Base" / "Basic": a weekly pool of Cursor models, shared with Grok. Size in tokens not written. | Prepaid top-up at per-model rates, or wait for the weekly reset. No third-party models. |
| Premium | 30 USD / month, or 300 USD / year | 4× Plus | Same top-up. Third-party models at API price, from the first request, on this prepaid credit. |
| Super | 100 USD / month, or 1,000 USD / year | 15× Plus | Same as Premium. |
| Ultra | 200 USD / month, or 2,000 USD / year | 50× Plus | Same as Premium. |
| Teams | 30, 100 or 200 USD per seat / month | The weekly pool of the Premium, Super or Ultra seat. A Free seat at 0 USD has no team allocation. | Shared prepaid balance. Third-party models: API price plus a Cursor Token Rate of 0.25 USD per million tokens, unless exempt. |
| Enterprise | Custom | "Pooled" | Same price family as Teams. The amount is not on the page. |

### Cursor, legacy plans

Prices on [Legacy Cursor plans](https://cursor.com/docs/account/pricing/legacy-cursor-plans). The size of the two monthly pools there is neither a number of tokens nor of requests.

| Plan | Price | Included pool | Beyond |
| --- | --- | --- | --- |
| Hobby | Free | Agent, chat and Tab, limited usage. Cursor Models pool only. | No on-demand. AI features stop until the reset. |
| Start (India only) | 649 INR / month, taxes included | Cursor Models pool. No Other Models pool. | No on-demand. |
| Pro | 20 USD / month | Two monthly pools: Cursor Models and Other Models. No "more" is quantified. | On-demand, off until enabled. Billed in arrears, at the same per-model rates, without markup, per [Usage-based charges](https://cursor.com/help/account-and-billing/overages). |
| Pro+ | 60 USD / month | Both pools, "more of each pool than Pro". | Same as Pro. |
| Ultra | 200 USD / month | Both pools, more than Pro. | Same as Pro. |
| Teams Standard | 40 USD / user / month | The Teams allocation of both pools, per seat. | On-demand on by default, can be capped. Cursor Token Rate on eligible third-party requests. |
| Teams Premium | 120 USD / user / month | 5× the Standard allocation. | Same as Standard. |

An even older generation counts requests, not tokens. Its monthly request quota is not written on [Request-based pricing (legacy)](https://cursor.com/docs/account/pricing/request-based-legacy). Overage and Max Mode there are at API price plus 20%.

### GitHub Copilot, usage-based billing

Prices and credits on [Plans for GitHub Copilot](https://docs.github.com/en/copilot/about-github-copilot/plans-for-github-copilot) and [Usage-based billing for individuals](https://docs.github.com/en/copilot/concepts/billing-and-usage/individuals/billing). The two individual URLs given at the start answer 301 to this second page.

| Plan | Price | Included pool | Beyond |
| --- | --- | --- | --- |
| Copilot Free | 0 USD | An allowance of GitHub AI Credits, no number. 2,000 code completions per month. Models via auto only. | The individual page says additional usage can be purchased. The number of included credits remains absent. |
| Copilot Student | 0 USD | An allowance of credits, no number. Unlimited completions. Auto only. No third-party agents. | Number of credits not written. |
| Copilot Pro | 10 USD / month (free for some) | 1,000 base credits + 500 flex = 1,500 / month | Dollar budget. 1 credit = 0.01 USD. Same per-token rate as the included usage. |
| Copilot Pro+ | 39 USD / month | 3,900 + 3,100 = 7,000 / month | Same. |
| Copilot Max | 100 USD / month | 10,000 + 10,000 = 20,000 / month | Same. |
| Copilot Business | 19 USD per granted seat / month | 1,900 credits per user / month, pooled | 0.01 USD per AI credit beyond the pool. |
| Copilot Enterprise | 39 USD per granted seat / month | 3,900 credits per user / month, pooled | 0.01 USD per AI credit. |
| Copilot access | 0 USD per seat with a GitHub Enterprise Cloud license; 10 USD / user / month as Copilot Standalone | No included credit | Metered usage billed separately, from the first use. |

### GitHub Copilot, premium requests (legacy)

Reserved, since 1 June 2026, for annual Copilot Pro and Copilot Pro+ subscribers who stayed on the old billing. Source: [Requests in GitHub Copilot (legacy)](https://docs.github.com/en/copilot/reference/copilot-billing/request-based-billing-legacy/copilot-requests).

| Plan | Price on this page | Premium requests | Beyond |
| --- | --- | --- | --- |
| Copilot Pro | 10 USD / month | 300 / month | Additional purchase at 0.04 USD per request (box ticked for Pro and Pro+). |
| Copilot Pro+ | 39 USD / month | 1,500 / month | Same. |

Business, Enterprise, Free, Student and Max do not appear in this table. No page read converts these requests into GitHub AI Credits.

## Cursor on sale

[cursor.com/pricing](https://cursor.com/pricing) shows Free, Plus, Premium, Super, Ultra, Teams and Enterprise. The JSON-LD `Offer` of the page carries Plus 8, Premium 30, Super 100, Ultra 200 and Teams 30, in USD. The cards give the month: Free 0, Plus 8, Premium 30, Super 100, Ultra 200. The Teams row of the comparison is written "$30+/user/month"; the docs specify the three seat prices.

The FAQ "How do usage limits work?":

> Every tier includes a pool of AI usage shared across Cursor and Grok. Within Grok, the pool covers Chat, Imagine, Voice, and Bot. Higher tiers include larger pools, and usage costs vary by product. Your allowance refreshes weekly, so one heavy day never wipes out your whole month.
>
> If you reach your included usage, you can keep going with top-offs, or wait for your allowance to refresh.

The comparison names the usage: Limited, Basic, 4x Plus, 15x Plus, 50x Plus, 4-50x Plus, Pooled. Free lists "Limited agent requests" and "Limited Chat requests", without a quota. Prices "do not include applicable taxes or fees (e.g. VAT)". Third-party models "must be purchased separately". No amount in EUR appears in the HTML.

[Models & Pricing](https://cursor.com/docs/models-and-pricing) holds the detail. The opening:

> Cursor is sold as part of The X Subscription, one subscription for Grok, Cursor, and X. Paid plans include a weekly pool of usage for Cursor models, shared across Grok and Cursor. Third-party models aren't included; on Premium and above you can use them at API list price, paid from prepaid credit.

Table of the page:

| Plan | Price | Included usage | Third-party models |
| --- | --- | --- | --- |
| Free | $0 | Limited | Not available |
| Plus | $8/mo | Base | Not available |
| Premium | $30/mo | 4x Plus | API list price, from prepaid credit |
| Super | $100/mo | 15x Plus | API list price, from prepaid credit |
| Ultra | $200/mo | 50x Plus | API list price, from prepaid credit |
| Teams | $30, $100, or $200 per seat/mo | Matches the seat's tier | API list price plus the Cursor Token Rate on eligible models |
| Enterprise | Custom | Pooled | API list price plus the Cursor Token Rate on eligible models |

The annual price, on the same page, excluding tax: 80 USD (Plus), 300 USD (Premium), 1,000 USD (Super), 2,000 USD (Ultra).

Plus has only the Cursor models, with fixed settings: Fast off, medium effort, Grok 4.7 in a 256k window. No Auto, Cloud Agents, Automations, Bugbot, Origin, third-party models, or personal API keys. Premium adds these surfaces and the third-party models. Super adds priority access to the latest releases. Ultra adds the earliest access to products and models.

The paid pool covers Grok 4.7, Grok 4.6, Grok 4.5 and Composer 2.5. It is shared among the agent in the editor, the CLI, Cloud Agents, Grok Bot and the Grok apps. It renews every week. "Different models draw from the pool at different rates." The page does not say how many tokens, dollars or requests "Base" contains.

When the weekly pool is used up, the same page offers to wait, to use the prepaid credit at the same per-model rates, or to move up a tier. "Requests are never downgraded in quality or speed." [Usage and limits](https://cursor.com/help/models-and-usage/usage-limits) adds that the prepaid credit does not reset with the week: each top-up remains available until used or expired, "currently 365 days after purchase". Unused included usage does not carry over.

[Usage-based charges](https://cursor.com/help/account-and-billing/overages) describes the top-ups. One pays at purchase or when Auto Top-up triggers, not in arrears. The credit pays for third-party models from the first request (Premium and above) and for Cursor models beyond the pool. A monthly cap on additional usage stops this usage even if credit remains. The marketing page says "top-offs"; the docs say "top-ups".

### Rates that draw on the pool

The sentence "These rates apply when you buy additional usage beyond the weekly pool" follows the table of Cursor models. The sentence "All prices are per million tokens" is written, on this page, immediately before the table of third-party models, not in the header of the Grok / Composer table. [Team pricing](https://cursor.com/docs/account/teams/pricing) writes "All prices are per million tokens" for the prices it displays, which repeat both families.

Rates of the Cursor models, as the cells of the [Models & Pricing](https://cursor.com/docs/models-and-pricing) table give them. A dash means the cache write cell is empty.

| Model | Input | Cache write | Cache read | Output |
| --- | --- | --- | --- | --- |
| Grok 4.7 | 2 | none | 0.5 | 6 |
| Grok 4.7 (Fast) | 4 | none | 1 | 12 |
| Grok 4.7 500k | 4 | none | 1 | 12 |
| Grok 4.7 500k (Fast) | 6 | none | 1.5 | 18 |
| Grok 4.6 | 2 | none | 0.5 | 6 |
| Grok 4.6 (Fast) | 4 | none | 1 | 12 |
| Grok 4.5 | 2 | none | 0.5 | 6 |
| Grok 4.5 (Fast) | 4 | none | 1 | 18 |
| Composer 2.5 | 0.5 | none | 0.2 | 2.5 |
| Composer 2.5 (Fast) | 3 | none | 0.5 | 15 |

The visible third-party table starts with Claude Fable 5.2 (8 / 10 / 0.2 / 40), Claude Haiku 5.5 (0.1 / 0.125 / 0.01 / 0.5), Claude Opus 5.5 (4 / 5 / 0.2 / 20), Claude Sonnet 5.5 (2 / 2.5 / 0.1 / 10), then Gemini, GPT-5.6 and Muse Wonder. A "Show more models" control follows. This note does not copy the collapsed rows.

Regional data residency adds 10% to the price of eligible models, a sentence from the same page.

### Teams, Auto, and Cursor token rate

[Team pricing](https://cursor.com/docs/account/teams/pricing): "There are two business plans: Teams and Enterprise (Custom)." There is no plan named Business. The paid seats are Premium 30, Super 100 ("3.75x Premium") and Ultra 200 ("12.5x Premium") USD per user per month. The Free seat is at 0: no team allocation; usage comes from the personal plan, or from the small free allowance if there is none. The weekly pools are not shared between seats. Enterprise is the plan with the common pool; its price is not quantified.

The Cursor Token Rate is 0.25 USD per million tokens, on Teams and Enterprise, on top of the API price, including with BYOK. It applies to input, output and cache tokens. It does not apply to individual plans, nor to Cursor models (Grok, Composer), nor to Cost mode. It does not apply either to third-party models whose cache read price is strictly below 0.10 USD per million tokens on all tiers.

Auto has three modes, described on [Models & Pricing](https://cursor.com/docs/models-and-pricing) and [Usage and limits](https://cursor.com/help/models-and-usage/usage-limits). Cost, "classic Auto", "bills as a Cursor-model request and draws from your weekly pool whatever model serves it". Individual plans have only Cost. On Teams and Enterprise, with Cursor Router, Balance and Intelligence bill at the list price of the routed model: a Cursor model draws from the pool, a third-party model draws from the prepaid credit, with the Cursor Token Rate unless exempt.

## Cursor legacy

[Legacy Cursor plans](https://cursor.com/docs/account/pricing/legacy-cursor-plans):

> Legacy Cursor plans are the plans Cursor sold before The X Subscription: Hobby, Start, Pro, Pro+, Ultra, and Teams with Standard and Premium seats. They keep working as they are. New customers start on The X Subscription.

The switch is optional and permanent. The plan, the price and the features continue as long as one does not change or cancel.

The page says that each paid plan of The X Subscription "includes at least twice as much coding usage with Cursor models as the legacy plan it replaces", and that it removes the third-party model allowance. "Twice" is not converted into tokens. The tier mapping is written in the table: Hobby to Free, Start to Plus (analogy only), Pro to Premium, Pro+ to Super, Ultra to Ultra, Teams Standard to the Premium seat, Teams Premium to the Super seat. An already-paying Cursor account is offered Premium and above; Pro, Pro+, Teams Standard and Teams Premium default to the tiers in the table.

Both pools reset with the monthly billing cycle.

> Cursor Models. Included usage for Grok 4.7, Grok 4.6, Grok 4.5, and Composer 2.5. Every legacy plan includes this pool.
>
> Other Models. Included usage for third-party models, drawn down at each model's API price. Pro, Pro+, Ultra, and Teams include this pool. Start and Hobby don't.

Choosing a third-party model, or letting Auto route to one, draws on Other Models at list price. [Usage-based charges](https://cursor.com/help/account-and-billing/overages): "Each legacy plan includes a monthly usage budget. If you exceed your included usage, additional requests are billed at API rates with no markup, in arrears each month." Pro, Pro+ and Ultra must enable this on-demand. Teams has it by default. Start and Hobby do not: AI stops until the reset. A brief overrun above a spending cap is credited temporarily, then may be billed if the cap is raised in the same cycle. It is not a refund.

Hobby includes Agent, Chat and Tab, not Cloud Agents, Bugbot or Origin. Start is 649 INR per month, taxes included, by UPI or card, Cursor Models pool only, without Auto, without Bugbot, without on-demand. Pro, Pro+ and Ultra have both pools, unlimited Tab, Bugbot, Cloud Agents, Automations, Origin and the Cursor SDK. Pro+ and Ultra have more of each pool than Pro; the page does not give the multiple. Teams Standard is 40 USD per user per month; Teams Premium 120 USD, "5x the Standard allowance". Usage is per seat, does not transfer, and follows the team's cycle.

### Requests, earlier generation

[Request-based pricing (legacy)](https://cursor.com/docs/account/pricing/request-based-legacy):

> The request-based pricing model charged users based on the number of AI requests made rather than token usage. Each license had a monthly allotment of requests.

The number of this allotment is not on the page.

> A request represents a single message sent to most models, which includes your message, any relevant context from your codebase, and the model's response.

On-demand and Max Mode are "at the model's API rate plus 20%". [Max Mode on legacy plans](https://cursor.com/help/ai-features/max-mode) repeats API plus 20%, and limits Max Mode on these plans to per-request billing. Current plans do not include it, says [Usage and limits](https://cursor.com/help/models-and-usage/usage-limits).

The visible table of request multipliers, Requests column:

| Model | Requests |
| --- | --- |
| Composer 2.5 | 2 |
| Gemini 3.1 Pro | 1 |
| Gemini 3.8 Flash | 1 |
| Gemini 4 Argon | 1 |
| Claude Fable 5.2, Claude Haiku 5.5, Claude Opus 5.5, Claude Sonnet 5.5, GPT-5.6 Luna, GPT-5.6 Sol, GPT-5.6 Terra, Grok 4.5, Grok 4.6, Grok 4.7, Muse Wonder | none |

The page does not gloss the dash. It says that a customer still on this model keeps it until the next renewal, then moves to usage-based billing. This "plus 20%" does not apply to the on-demand text of the Pro / Pro+ / Ultra plans, which is "no markup". These are two pages, two generations.

## GitHub Copilot in credits

The search `https://docs.github.com/en/search?query=%22Copilot+credits%22`, read on 2026-11-10, shows 0 results for the phrase "Copilot credits". The pages that hold the numbers speak of GitHub AI Credits, or AI credits.

[Plans for GitHub Copilot](https://docs.github.com/en/copilot/about-github-copilot/plans-for-github-copilot) lists Copilot Free, Copilot Student, Copilot Pro, Copilot Pro+, Copilot Max, Copilot Business, Copilot Enterprise, and Copilot access. Copilot is not available for GitHub Enterprise Server.

The overview table gives, for credits: Free and Student "An allowance of GitHub AI Credits"; Pro "Base: 1,000"; Pro+ "Base: 3,900"; Max "Base: 10,000"; Business "Total per user per month: 1,900"; Enterprise "Total per user per month: 3,900"; Copilot access "No included allowance". This overview table shows only the base for individual plans. The detailed table, further down on the same page and repeated by the billing page, adds the flex.

| Plan | Price per month | Base credits | Flex allotment | Total monthly AI credits |
| --- | --- | --- | --- | --- |
| Copilot Pro | $10 USD | 1,000 | 500 | 1,500 |
| Copilot Pro+ | $39 USD | 3,900 | 3,100 | 7,000 |
| Copilot Max | $100 USD | 10,000 | 10,000 | 20,000 |

| Plan | Price per granted seat per month | GitHub AI Credits per user per month |
| --- | --- | --- |
| Copilot Business | $19 USD | 1,900 |
| Copilot Enterprise | $39 USD | 3,900 |

The plans page, on Business and Enterprise: "These licenses contribute credits to a shared pool, and usage beyond the pool is charged at $0.01 USD per AI credit."

Copilot access: same feature and model eligibility as Copilot Enterprise, "with a $0 seat charge for users with a GitHub Enterprise Cloud license or $10 USD per user per month for Copilot Standalone. Metered usage is billed separately, with no included allowance." It cannot coexist with Business or Enterprise seats in the same enterprise. [Usage-based billing for organizations and enterprises](https://docs.github.com/en/copilot/concepts/billing-and-usage/organizations-and-enterprises/billing) says that usage draws first from the pool, then is billed separately. An example from the page: 100 Business users make a pool of 190,000 credits, not 100 buckets. Adding licenses mid-cycle enlarges the pool immediately; removing them shrinks it only at the next cycle.

[github.com/features/copilot/plans](https://github.com/features/copilot/plans), FAQ, repeats 1 credit = 0.01 USD, the prices 0 / 10 / 39 / 100 USD, and for Free: "limited to 2,000 completions and access to entry level models per month". The plans docs write "Limited to 2000 completions per month on Copilot Free", without a comma. No EUR on these HTML pages.

### What the individual page calls a credit

[Usage-based billing for individuals](https://docs.github.com/en/copilot/concepts/billing-and-usage/individuals/billing):

> When you use Copilot, the interaction consumes tokens: input tokens (what's sent to the model), output tokens (what the model generates), and cached tokens (context the model reuses or stores). Each token is priced based on the model used.
>
> This total is converted into AI credits (1 AI credit = $0.01 USD).

Base credits "match with your subscription price and they never change". Flex "is a variable part of your included usage" and can change with model economics. Base is consumed first, then flex, at the same rates in the IDE, on GitHub.com and in the CLI. Included usage does not carry over. The reset is on the 1st of the month at 00:00:00 UTC, independent of the subscription date.

Free and Student have an allowance and auto only. "Copilot Free includes 2000 code completions per month and Copilot Student includes unlimited code completions." The number of credits of these two plans is not in the table.

What consumes credits: Copilot Chat, Copilot CLI, Copilot cloud agent, Copilot Spaces, Spark, third-party agents. Completions and next edit suggestions do not consume any and remain unlimited on all paid plans. The plans page says the same for Business, Enterprise and Copilot access, including at a 0 USD budget or a reached cap.

The agent does not have a fixed cost in credits. The page:

> Agentic features: Features like agent mode and Copilot cloud agent can involve multiple model calls within a single task. A complex agentic session working across a large codebase will consume significantly more usage than a quick question in chat.

Two discounts, on the same page, for paid plans: 30% on the model cost with Copilot Fusion until 30 November 2026; 10% with auto selection in Copilot Chat or Copilot cloud agent.

When exhausted, an individual plan can move up a tier (one pays only the difference, usage already made counts in the new allowance), set an additional usage budget, or wait for the following month. "1 AI credits = $0.01 USD, so a $10 budget covers 1,000 AI credits." Additional usage can be capped; to continue, what has already been consumed must be settled. A Business or Enterprise user who empties the credits sees a banner to ask an administrator for budget, not the same path. Usage beyond the included amount is billed afterwards, separately from the renewal, including after cancellation.

[Models and pricing for GitHub Copilot](https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing) repeats the conversion and the rate card: "All prices are per 1 million tokens." Usage beyond the allowance, "for any Copilot plan", is billed in AI credits at these rates, still at 1 credit = 0.01 USD. The cost of an interaction is therefore the token price, then the division by 0.01 USD. The page sets no number of credits per request, nor per agent turn.

Excerpts of the rate card, per million tokens, default tier. The page also has "Long context" tiers (often double on input beyond a threshold, 200K or 272K depending on the model) and promo notes.

| Model | Input | Cached input | Cache write | Output |
| --- | --- | --- | --- | --- |
| GPT-5.6 Luna (≤ 200K) | 0.20 | 0.02 | 0.25 | 1.20 |
| GPT-5.6 Sol (≤ 272K) | 4.00 | 0.40 | 5.00 | 20.00 |
| GPT-5.6 Terra (≤ 272K) | 2.00 | 0.20 | 2.50 | 12.00 |
| Claude Haiku 5.5 (≤ 100K) | 0.10 | 0.01 | 0.125 | 0.50 |
| Claude Sonnet 5.5 | 2.00 | 0.10 | 2.50 | 10.00 |
| Claude Opus 5.5 | 4.00 | 0.20 | 5.00 | 20.00 |
| Claude Fable 5.2 | 8.00 | 0.20 | 10.00 | 40.00 |
| Gemini 3.8 Flash | 0.75 | 0.075 | none | 3.75 |
| Gemini 4 Argon | 2.00 | 0.10 | none | 10.00 |
| Grok 4.7 (≤ 200K) | 2.00 | 0.50 | none | 6.00 |
| Kimi K3 | 3.00 | 0.30 | none | 15.00 |

Gemini 3.8 Flash is at the indicated promo price until 31 December 2026. Gemini 4 Argon until 1 December 2026. Grok 4.5 and Grok 4.6 have the same figures as Grok 4.7 on this page, with a long-context tier at 4 / 1 / 12 beyond 200K. The Google table has no cache write column.

Code review is the exception where the model is not shown. The page gives ranges in dollars per review, not in credits: Lite about 0.05 to 1 USD (average 0.25), Balanced 0.25 to 5 (average 1.50), Max 1.50 to 25 (average 10). These are estimates. Review also consumes GitHub Actions minutes.

## Premium requests, after 1 June 2026

[What changed with Copilot billing (legacy)](https://docs.github.com/en/copilot/reference/copilot-billing/request-based-billing-legacy/what-changed-with-billing):

> Before June 1, 2026, billing was premium request-based: each model interaction cost one premium request unit (PRU), and a multiplier was applied based on which model you used, more powerful models used more premium requests.
>
> As of June 1, 2026, GitHub replaced request-based billing with usage-based billing, where the cost of an interaction depends on two things: the model and the number of tokens consumed.

An annual Pro or Pro+ subscriber can stay on premium requests until the end of the year, then falls back to Copilot Free; cancel with a prorated refund; or switch to a monthly plan with a prorated credit. Staying means continuing the count in premium requests and multipliers. The page gives no exchange rate to AI credits.

[Requests in GitHub Copilot (legacy)](https://docs.github.com/en/copilot/reference/copilot-billing/request-based-billing-legacy/copilot-requests) limits the article to these annual subscribers who stayed on the old model after 1 June 2026. A request is a prompt. For the agent:

> For agentic features, only the prompts you send count as premium requests; actions Copilot takes autonomously to complete your task, such as tool calls, do not.

The consumption table: Chat (ask, edit, agent, plan) = one premium request per prompt, times the model rate. CLI = one with the default model, otherwise times the rate. Code review = 13. Cloud agent = one per session, times the rate, plus one per steering comment during the session. Spaces = one per prompt, times the rate. Codex in VS Code and third-party agents, in preview = one per prompt, times the rate.

Allowances from this page only: Pro 300 per month, Pro+ 1,500 per month. The line "Purchase additional premium requests at $0.04/request" is ticked Included for both columns. The counters reset on the 1st at 00:00:00 UTC. Unused does not carry over. When exhausted, one can still use an included model, subject to rate limiting, or set a budget, or move up a plan. Additional purchase is not available if the Pro or Pro+ subscription went through GitHub Mobile on iOS or Android.

[Model multipliers for annual plans on request-based billing (legacy)](https://docs.github.com/en/copilot/reference/copilot-billing/request-based-billing-legacy/model-multipliers-for-annual-plans): these multipliers "do not apply to GitHub's new usage-based billing model". Legacy annual subscribers do not have the new models. Auto in Chat, CLI, the app or the cloud agent: 10% discount, so a 1× model is counted 0.9×.

| Model | Multiplier |
| --- | --- |
| Claude Haiku 4.5 | 0.33 |
| Claude Opus 4.8 | 27 |
| Claude Sonnet 4.6 | 9 |
| GPT-5.3-Codex | 6 |
| MAI-Code-1.1-Flash | 0.25 |

Code review has a multiplier of 13, repeated here. [Monitoring your GitHub Copilot usage and entitlements (legacy)](https://docs.github.com/en/copilot/reference/copilot-billing/request-based-billing-legacy/monitor-premium-requests) explains where to read the counter. The requested URL `https://docs.github.com/en/copilot/how-tos/manage-and-track-spending/monitor-premium-requests` answers 301 to this one. It does not restate the 300, the 1,500, nor a rate to credits.

[Overview of request-based billing (legacy)](https://docs.github.com/en/copilot/reference/copilot-billing/request-based-billing-legacy/github-copilot-premium-requests) says that some models consume several premium requests, with the example "5× or 20× the standard rate", and that overage, if overages are enabled, is billed "at the standard rate". The 0.04 USD per request remains the one from the table of the Requests page. To block paid usage without canceling the annual subscription, the page says to set the premium request budget to 0 USD and to enable "Stop usage when budget limit is reached".

## Units

Cursor and Copilot do not write a common token. Each prices its own tokens, per million, on its model page. One million Grok 4.7 input tokens is at 2 USD on both sides in the tables read; this is not a sentence saying the units are interchangeable, and the columns do not overlap (for Grok, Copilot has no cache write column, which Cursor leaves empty).

Conversions written, and only those:

- GitHub, current billing: 1 AI credit = 0.01 USD. The amount in credits is the token cost divided by that cent. Source: [Usage-based billing for individuals](https://docs.github.com/en/copilot/concepts/billing-and-usage/individuals/billing) and [Models and pricing](https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing). A 10 USD budget covers 1,000 credits, same sentence.
- GitHub, Business and Enterprise, beyond the pool: 0.01 USD per AI credit. Source: [Plans](https://docs.github.com/en/copilot/about-github-copilot/plans-for-github-copilot).
- GitHub, legacy premium requests: not a credit-per-request rate. Overage is 0.04 USD per premium request, and the multiplier is a number of premium requests, not of credits. Sources: [Requests](https://docs.github.com/en/copilot/reference/copilot-billing/request-based-billing-legacy/copilot-requests) and [Model multipliers](https://docs.github.com/en/copilot/reference/copilot-billing/request-based-billing-legacy/model-multipliers-for-annual-plans).
- Cursor, current pool: no "1 request = N tokens" factor. Cost counts as one Cursor model request on the pool, whichever model answers. The other draws follow the per-million-token prices. Sources: [Models & Pricing](https://cursor.com/docs/models-and-pricing) and [Usage and limits](https://cursor.com/help/models-and-usage/usage-limits).
- Cursor, legacy requests: 1 message = 1 request for most models, then the Requests column (1, 2, or dash). The on-demand of this generation is the API price plus 20%. It is not a rate to the weekly pool. Source: [Request-based pricing (legacy)](https://cursor.com/docs/account/pricing/request-based-legacy).
- Cursor Teams / Enterprise: 0.25 USD per million tokens extra, on eligible third-party models. Source: [Models & Pricing](https://cursor.com/docs/models-and-pricing).

## Not on the pages read

- A price in EUR, for either product. The only non-USD price found is Start, 649 INR taxes included.
- The size of Cursor's "Base" / "Basic" / "Limited" pool, in tokens, in dollars or in requests. The 4×, 15× and 50× are relative to Plus, and Plus has no magnitude.
- The size of the monthly pools of legacy Hobby, Pro, Pro+ and Ultra. "More than Pro" and "at least twice" are not counts.
- The monthly request allotment of Cursor's per-request billing, and the meaning of the dash in the Requests column.
- The number of GitHub AI Credits included in Copilot Free and Copilot Student.
- An official rate "N credits = 1 premium request", or "1 Copilot credit = 1 token", or "1 Cursor token = 1 Copilot token".
- A Cursor plan named Business. The Teams page says the two business plans are Teams and Enterprise.
- Hobby, Pro and Pro+ as Cursor plans open to new customers. The legacy page says new customers start on The X Subscription.
