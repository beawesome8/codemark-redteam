# CODEMARK Evaluation — LLM-Driven Adversarial Testing

This document reports one specific test: **does CODEMARK's watermark survive
ordinary LLM code cleanup, when the LLM is never told the watermark exists?**

Short answer: no, in 3 of 4 cases — and the reason why is more interesting
than a plain pass/fail.

---

## 1. Why this test, specifically

CODEMARK v0.1 (see `README.md`) is a structural watermark that encodes bits
in the choice between AST-equivalent code forms (`x = x + 1` vs `x += 1`,
`{}` vs `dict()`, `if/else` vs ternary). The design doc (`SECRET_SAUCE.md`)
already identifies its known worst-case failure: an attacker who knows the
method and deliberately normalizes every carrier to one canonical form
destroys the watermark (measured: 49% match, indistinguishable from chance).

That known failure assumes an *adversary* — someone with knowledge of the
method and intent to strip it. This evaluation asks a different, more
realistic question: **does an ordinary developer, with zero knowledge of
the watermark and zero adversarial intent, trigger the same failure by
accident, just by asking an LLM to clean up their code?**

This matters more than the adversarial case in practice. Per the EU AI Act
threat model this project references, "reasonable modifications" are
things developers actually do — and in 2026, "paste it into an LLM and ask
for a cleanup pass" is one of the most common things developers actually do.

## 2. Method

- **Target file:** `sample_real_2.py`, a 298-line file pulled from an
  existing personal project (PromptGuard's eval runner), not written for
  this test.
- **Watermark:** 8-bit payload embedded via CODEMARK's keyed HMAC scheme
  across 9 available carrier sites (`EmptyCollectionCarrier`,
  `AugAssignCarrier`, `TernaryCarrier`).
- **Attacks:** four prompts sent to Claude (`claude-sonnet-4-6`) via the
  Anthropic API, **none of which mention watermarking, security, or
  detection.** Each simulates an ordinary, plausible developer action:

  | Attack | Prompt intent |
  |---|---|
  | `paraphrase` | "Rewrite to be more readable and idiomatic, preserve behavior." |
  | `refactor_style` | "Refactor to modern Python best practices (type hints, cleaner conditionals)." |
  | `cleanup` | "Remove redundant code, simplify logic, improve formatting." |
  | `add_comments` | "Add docstrings and inline comments, don't change logic." |

- **Measurement:** for each attack output, re-run `find_carriers()` and
  `detect()` against the LLM's rewritten file, and separately compare the
  distribution of carrier *forms* before vs. after (`carrier_form_summary()`
  in `watermark.py`) to distinguish "watermark destroyed" from "watermark
  destroyed *in a specific, explainable direction*."
- Deliberately excluded from prompts: any instruction resembling "remove
  hidden patterns" or "make this code look human-written" — that would test
  a different, harder threat model (an informed adversary), not this one.

## 3. Results

| Attack | Valid Python? | Carriers found | Bits recovered | Watermark survived? |
|---|---|---|---|---|
| `paraphrase` | Yes | 9 (same as original) | 6/8 correct | No |
| `refactor_style` | Yes | 10-11 | partial (varies by run — see §4) | No |
| `cleanup` | Yes | 10 | partial (varies by run — see §4) | No |
| `add_comments` | **No** — invalid syntax (truncated output) | — | — | N/A — harness limitation, not a result |

Baseline (no attack, freshly watermarked file): payload recovered exactly,
0 errors.

**The carrier count staying roughly stable (9 → 9-11) while bit accuracy
drops is the important detail.** It rules out the simplest explanation
(the LLM deleted the carrier-bearing code). The carriers are still there.
What changed is *which form* each carrier is in.

## 4. Why it fails: carrier-form collapse, not carrier loss

Directly measuring the form distribution (literal/expanded vs.
constructor/augmented/ternary) before and after each attack:

| Carrier type | Watermarked (baseline) | After `paraphrase` | After `refactor_style` | After `cleanup` |
|---|---|---|---|---|
| `EmptyCollectionCarrier` | 2 literal / 2 constructor | **3 / 0** | **4 / 0** | **3 / 0** |
| `AugAssignCarrier` | 0 expanded / 2 augmented | 0 / 1 | **0 / 2** | **0 / 2** |
| `TernaryCarrier` | 2 expanded / 2 ternary | 1 / 4 | 1 / 4 | 1 / 4 |

Across all three valid attacks: empty-collection carriers collapse to
**100% literal form** (`{}`/`[]` over `dict()`/`list()`), augmented-assignment
carriers collapse to **100% augmented form** (`+=` over `x = x + e`), and
ternary carriers skew heavily toward ternary form. These are, respectively,
the forms style guides and linters (PEP 8, `black`, common "idiomatic
Python" guidance) already recommend.

**Conclusion:** the LLM isn't attacking the watermark. It's doing its
actual job — writing conventional Python — and conventional Python
happens to sit at one specific pole of every carrier this design uses.
The B2 "full normalization" attack that CODEMARK's own design doc already
flagged as a known worst case is not a hypothetical sophisticated-attacker
scenario. It is the default output of asking an LLM to clean up code,
with no adversarial framing required.

Three independent prompts, worded differently, converged on the same
direction for every carrier type. That consistency — not any single
number — is the actual finding.

## 5. What this does and doesn't mean

**Does mean:** a structural watermark of this kind cannot rely on carrier
forms that coincide with common style conventions. Any carrier family
where one form is "the idiomatic one" is fragile against LLM-assisted
cleanup specifically, independent of whether anyone is trying to remove
the watermark.

**Does not mean:** the underlying mechanism (keyed HMAC, AST-level
embedding, majority-vote detection) is broken. Those parts worked exactly
as designed — the false-positive rate, functional equivalence, and clean
detection on the unmodified file all held (see `README.md` §4). The
failure is specifically in carrier *selection*, not in the embedding or
detection logic.

**Does not mean** this generalizes to all code or all LLMs without further
testing — this is one file, one model, one run per attack (LLM outputs are
non-deterministic; a second run of the same prompt would likely produce a
different exact bit pattern, though the same directional collapse would be
the expectation based on why it happens).

## 6. Implications for a fix (not yet built)

Two directions, in priority order:

1. **Choose carrier families without a socially "correct" pole.** E.g.
   `x = x + 1` vs `x += 1` has a clear idiomatic winner; something like
   variable-independent statement reordering (where dependency analysis
   permits) has no equivalent style convention pulling it one way.
2. **Detect normalization itself as a signal**, per the original design
   doc's mitigation: a file where every carrier sits at the canonical pole
   is statistically anomalous even without a working watermark — "absent"
   and "scrubbed" are different, detectable states. Not implemented in
   v0.1; this evaluation is the evidence for why it would matter.

Neither is built in this version. This document reports the finding, not
a shipped fix.

## 7. Harness limitations (methodology caveats, not watermark findings)

- `add_comments` failed on invalid Python output due to `max_tokens=4000`
  truncation in the harness, not a watermarking result — needs a token
  limit increase or `stop_reason` check to be usable.
- Attack outputs are non-deterministic between API calls; the bit-pattern
  numbers in §3 reflect one generation each, not an average. The
  form-collapse direction in §4 was independently reproduced across
  separate generations of `paraphrase`, `refactor_style`, and `cleanup`,
  which is why that's reported as the headline result rather than the
  exact bit patterns.
- Single file, single model (`claude-sonnet-4-6`), single key. No
  statistical claim (e.g. a p-value on survival rate) is made here — this
  is a documented case study, not a benchmark.

## 8. Reproduce

```bash
python llm_attacks.py
python test_normalization_hypothesis.py
```

Requires `ANTHROPIC_API_KEY` set to a valid Anthropic key (starts
`sk-ant-`) in `.env`, not exported at the shell or OS level where it can
silently shadow `.env` — see repo history for why that distinction matters
in practice.
