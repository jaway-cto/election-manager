# Methodology

How the model produces a single calibrated probability per council and a single best estimate of final seats per party.

## Inputs per council

- `seats_up`: number of seats being contested today
- `total_seats`: total council size (for context)
- `last`: 9-array of seats won by each party at last comparable election
  (Con, Lab, LD, Grn, Ref, SNP, PC, Ind, Oth)
- `pre`: 9-array of pre-election composition (current sitting council)
- `declared`: 9-array of seats already declared today (live, BBC + DC API)
- `pollcheck`: per-party central + low–high seat range from PollCheck MRP
- `status`: Pending / Counting / Partial / Complete / Verified

## Inputs national

- `swing[i]`: live observed national swing (size-weighted) computed from Complete councils
- `MARKET_IMPLIED[party]`: Polymarket-implied expected total seats per party

## Method 1 — Same-as-last (naive baseline)

For each council:

```
predicted[i] = declared[i] + last[i] * remaining / sum(last)
```

Pretends remaining seats break exactly like the last election. Useful only as a counterfactual reference. Mechanically wrong as soon as a real swing emerges.

## Method 2 — Swing-adjusted

For each council:

```
share[i] = max(0, last[i]/sum(last) + swing[i])
share = share / sum(share)                        # renormalise
predicted[i] = declared[i] + share[i] * remaining
```

Applies the live national swing in seat-share space. Better than method 1 once data arrives. **Critical flaw**: vote-share-to-seat-share is non-linear under FPTP. In counties (FPTP divisions), Reform passing 30% threshold sweeps wards rather than getting "30% of seats". Method 2 systematically under-predicts Reform breakthroughs in shire counties.

## Method 3 — PollCheck-hybrid

For each council:

```
if status in (Complete, Verified):
    predicted = declared
elif pollcheck has projection:
    predicted = pollcheck_central * seats_up / sum(pollcheck_central)
else:
    fallback to method 1 / 2
```

Uses PollCheck's FPTP-aware MRP-style projections for undeclared councils. Better on counties. **Limitation**: PollCheck centrals were published pre-poll using polling-based MRP and don't update intra-day. They tend to under-forecast whichever side has count-day momentum (Reform here).

## Method 4 — Market model (calibrated, primary)

What the Headline + Market model tab use.

### Step 1: Reform-only calibration to Polymarket aggregate

For each pending/partial council, compute aggregate PollCheck Reform total. Bisection-search a single scaling factor `s_ref` such that:

```
sum_over_pending_councils(
  pollcheck_central[Ref] * s_ref * remaining / (sum_pollcheck * (1 + extra_ref_share))
) ≈ MARKET_IMPLIED[Ref] - declared_total[Ref]
```

The renormalisation step (every council renorm to `seats_up`) absorbs the rest. Per-council party rankings preserved. Currently `s_ref ≈ 2.4×`.

Why Reform-only? Earlier multi-party calibration (e.g. Lab × 0.56) distorted per-council rankings (caused Greens to "beat" Lab in Brent — false). Reform-only is geographically neutral: the market's view of Reform's overshoot is the only systematic adjustment we apply; the rest falls out naturally via per-council renormalisation.

### Step 2: Per-council Monte Carlo

For each council with status Pending or Partial:

```
mean[i] = pollcheck_central[i] * scale[i] * remaining / sum(pollcheck * scale)   # per-council renorm
sigma[i] = (pollcheck_high[i] - pollcheck_low[i]) / 2.56                        # ~80% CI -> σ

for trial in 1..4000:
    momentum ~ N(0, 0.04 * seats_up)        # shared Reform-momentum factor
    for each party i:
        sample[i] = mean[i] + N(0, sigma[i])
        if i == Ref:    sample[i] += momentum
        if i in (Con, Lab):  sample[i] -= momentum / 2
    sample = max(0, sample)
    total = declared + sample
    total = total * seats_up / sum(total)   # renorm to council size
    winner_i = argmax(total)
    win_count[winner_i] += 1

P(party wins this council) = win_count / 4000
expected_seats[party] = mean(total over trials)
```

For status Complete/Verified councils: deterministic. P(declared winner) = 99%; 1% spread over other parties present.

### Step 3: Aggregate sense check

Sum expected seats across all 144 councils. Compare to `MARKET_IMPLIED` per party. Currently within 50 seats on Reform (the calibration target). Other parties drift by 100-300 seats — acceptable because their thresholds aren't priced sharply enough to calibrate against.

## Why methods diverge — and which to trust

| Method | Reform total | Lab total | Trustworthy on |
|---|---:|---:|---|
| 1 Same-as-last | 343 | 2,315 | nothing — baseline only |
| 2 Swing-adjusted | 1,340 | 1,572 | non-county districts |
| 3 PollCheck-hybrid | 1,312 | 1,312 | counties (FPTP-aware) |
| 4 Market model | 1,576 | 1,159 | **best estimate; calibrated to live market** |

Live data over a session keeps narrowing all four. For decisions, use Method 4. The Headline tab's "Predicted final" column is exactly Method 4.

## Per-council probability output

The Market model tab outputs for each council:

- Modal winner (argmax of MC win counts)
- Win P
- Runner-up + Runner P
- BBC winner (council CONTROL — different metric, for cross-validation)
- BBC flash (e.g. "Reform UK gain from Conservative")
- P(Con) ... P(Oth) — full per-party distribution
- E[seats per party] — expected seat count

A market-maker pricing per-council winning-party markets (which don't exist publicly) would use the per-party P columns directly as fair-value odds.

## Open problems

1. **Geographic decomposition of swing**: currently national swing applied uniformly. Real-world: London Lab won't drop by the same magnitude as northern mets Lab. A region-specific swing model (mets / districts / counties / London separately) would be more accurate, but data is too sparse early in the count to fit reliably.

2. **Multivariate party correlations**: MC samples are independent within each council (with one shared Reform momentum scalar). Real Con↔Ref ↔ Lab correlations are richer. Would benefit from a Dirichlet-style draw or multivariate normal over the simplex.

3. **PollCheck range interpretation**: treated as ~80% CI to derive σ. PollCheck don't publish their CI level explicitly. If their range is tighter (e.g. 50% CI), σ should be wider, MC probabilities less crisp.

4. **MARKET_IMPLIED hand-tuning**: Polymarket only prices certain thresholds (e.g. Lab >700+ but no >800+). Implied means are derived by hand from the threshold ladder. Should automate by reading every active Polymarket contract for the event and integrating P(X ≥ t) over t.
