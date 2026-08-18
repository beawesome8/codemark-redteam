# CODEMARK

**Keyed, multi-bit, structure-level watermarking for Python source code.**

Status: `v0.1` — working prototype, measured, not production-hardened.

**Headline finding:** the watermark survives hand-scripted "reasonable
modification" edits perfectly (§4), but is defeated by ordinary,
non-adversarial LLM code cleanup — no attacker, no watermark-aware prompt,
just "make this idiomatic." Full evidence in [`EVALUATION.md`](./EVALUATION.md).

---

## 1. What this is

CODEMARK embeds an attributable payload (e.g. vendor / model / session ID)
into Python source code by choosing between AST-equivalent forms of the same
code — `x = x + 1` vs `x += 1`, keyword-argument order, `{}` vs `dict()` —
rather than by touching tokens, names, or whitespace. Detection reads which
form was chosen at each carrier site and recovers the payload.

It exists to test one specific claim: **a watermark that lives in the choice
between semantically equivalent program forms survives the edits a developer
plausibly makes in the first hour** (auto-format, rename, comment, insert
code) — because none of those edits change *which form was chosen*, only
*how it's written or named*.

## 2. What this is not

- Not neural / model-level watermarking (contrast: RoSeMary, SrcMarker).
- Not resistant to an attacker who knows the method and deliberately
  normalizes every carrier to one canonical form (see §5).
- Not a compliance product. No metadata layer, no fingerprinting layer, no
  legal claim of EU AI Act Article 50 conformance. If you need that, this is
  one candidate technical layer among several required ones — not the whole
  system.
- Not peer-reviewed. Numbers below are a single-run measurement on one
  122-line file, reproducible via `run_demo.py`, not a benchmark suite.

## 3. How it works (mechanism, not hand-waving)

| Step | What happens |
|---|---|
| 1. Carrier discovery | Walk the AST, find sites with a grammar-legal equivalent-form choice. v0.1: augmented assignment, kwarg order, empty-collection style. |
| 2. Key the channel | `HMAC(secret_key, site_index) & 1` decides which form encodes 0 vs 1 at each site — without the key, an attacker can't tell which form is which. |
| 3. Encode payload | 12-bit payload (vendor / model / session), repeated ~3.25× across carriers, interleaved so localized damage doesn't wipe one field. |
| 4. Embed | `NodeTransformer` flips only the carriers whose current form disagrees with the target, re-emits via `ast.unparse`. |
| 5. Detect | Reparse, read carrier states, un-key, majority-vote each bit across a small alignment search (handles code inserted above the watermark shifting carrier indices). |

Full derivation and paper lineage (KGW, CodeIP, MATRIX, STONE) is in the
build log, not repeated here — this README states results, not the sales
pitch.

## 4. Measured results

Target: `inventory.py`, 122 LOC, 39 carriers, 12-bit payload, single key.
All numbers from one run of `run_demo.py` on 2026-07-06 — not averaged
across a corpus.

| Test | Result | p-value |
|---|---|---|
| Unwatermarked baseline (false-positive check) | 50.0% match | 0.566 (not detected — correct) |
| Freshly watermarked | 39/39 | 1.8e-12 |
| Black auto-format | 39/39 | 1.8e-12 |
| Full variable rename | 39/39 | 1.8e-12 |
| Comment insertion | 39/39 | 1.8e-12 |
| All three combined | 39/39 | 1.8e-12 |
| New code inserted above (alignment search) | 39/39 at offset −1 | 1.8e-12 |
| Functional equivalence (exec + compare outputs) | equal | — |

**What this genuinely shows:** perfect recovery under the specific "realistic
developer edit" tier the EU AI Act's "reasonable modifications" language
plausibly means. That's the actual scope of the claim.

## 5. Where it breaks — read this before trusting it

| Attack | Result |
|---|---|
| Random carrier flips, 10% | 6/10 runs exact — degrades |
| Random carrier flips, 20% | 2/10 runs exact — degrades |
| Full normalization by an attacker who knows the method | destroyed (49%, p=0.632) — key or no key |
| **Ordinary LLM cleanup ("make this idiomatic"), no adversarial intent** | **destroyed in 3/4 tested prompts — full writeup in [`EVALUATION.md`](./EVALUATION.md)** |

The normalization row above was originally scoped as a worst-case
*adversarial* attack — someone who knows the method and deliberately
strips it. **[EVALUATION.md](./EVALUATION.md) shows this isn't just an
adversarial worst case — it's what happens by accident when a benign
"clean this code up" prompt is run through an LLM.** Three independently-
worded, non-adversarial cleanup prompts, tested against a real 298-line
file, all collapsed carrier forms toward the same canonical style pole
(e.g. empty-collection carriers → 100% literal form) with zero mention of
watermarking anywhere in the prompts. Read the evaluation doc before
deciding whether v0.1 is adequate for your use case — the honest answer,
based on what's actually been measured, is: not against any code that's
likely to pass through an LLM cleanup pass, which in 2026 is most code.

If your use case requires robustness against either a deliberate attacker
or ordinary LLM-assisted development workflows, **do not rely on v0.1
alone.**

## 6. Known v0.1 artifacts

- `ast.unparse` drops comments and normalizes quotes outside carrier sites
  (fix: swap for `libcst` — mechanical, not architectural).
- Repetition coding, not real error correction — B-tier rows above are the
  direct consequence; a BCH code is the v0.2 fix.
- No cryptographic ownership proof. There's an architectural slot for one
  (hash-commit the key now, prove possession later via ZK), not an
  implementation.

## 7. Reproduce

```bash
pip install black
python3 run_demo.py
```

```bash
python3 watermark.py embed  --key "..." --vendor 11 --model 7 --session 13 in.py out.py
python3 watermark.py verify --key "..." --vendor 11 --model 7 --session 13 out.py
```

## 8. File map

| File | Purpose |
|---|---|
| `watermark.py` | Carrier discovery, keyed embed, detect/verify, CLI |
| `attacks.py` | Realistic-edit suite (A1–A5) + adversarial tier (B1–B2) |
| `inventory.py` / `inventory_watermarked.py` | Demo target, before/after |
| `run_demo.py` | Reproduces every number in this README |
| `results.json` | Raw measurements |

## 9. Roadmap (priority order, not aspiration)

1. Replace repetition code with BCH + erasure handling — fixes the B1 rows directly.
2. Swap `ast.unparse` → `libcst` for byte-level fidelity outside carrier sites.
3. Expand carrier families (ternary/if-else, chained comparisons, f-string/format, comprehension/loop) — each one raises normalization cost for an attacker.
4. Publish a hash commitment to the key (provable existence at time T without revealing it) and scope the ZK circuit for "I hold the key that explains this match rate."
5. Run the full attack suite across a real corpus (~1,000 files of AI-generated Python) and publish that dataset — the corpus, not the algorithm, is the actual moat.
6. Build a corpus-relative normalization-anomaly detector. A per-file fixed skew threshold was implemented and tested — it does not work (natural, never-touched code hits the same 100% skew as attacked code; see [`EVALUATION.md` §7](./EVALUATION.md#7-attempted-mitigation-normalization-anomaly-detection--negative-result)). A corpus-relative version (flag outliers against a baseline distribution across many natural files, not against a fixed constant) is untried and requires a natural-code corpus this project doesn't yet have — real follow-up work, not a quick fix.

## 10. License / status

MIT License

please fee free to contribute, raise issues or request PR's
