"""
llm_attacks.py — LLM-driven adversarial evaluation harness for CODEMARK.

Generates realistic code-modification attacks using Claude (no mention of
watermarking in any prompt — these simulate an ordinary developer editing
the file, not an adversary specifically targeting the watermark) and
measures watermark survival rate against each.
"""
import os
import ast
from dotenv import load_dotenv
from anthropic import Anthropic
from watermark import embed, detect, find_carriers

load_dotenv()
client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# Each prompt describes an ordinary, plausible developer action.
# No prompt references watermarking, security, or detection — that would
# bias this into testing a different (much harder) threat model.
ATTACK_PROMPTS = {
    "paraphrase": (
        "Rewrite this Python file to be more readable and idiomatic, without "
        "changing its behavior. Keep the same function names and signatures. "
        "Return only the code, no explanation, no markdown fences."
    ),
    "refactor_style": (
        "Refactor this Python file to follow modern Python best practices "
        "(type hints, cleaner conditionals, better variable names where "
        "unclear). Preserve exact behavior. Return only the code, no "
        "explanation, no markdown fences."
    ),
    "cleanup": (
        "Clean up this Python file — remove redundant code, simplify "
        "logic where possible, improve formatting. Preserve exact behavior. "
        "Return only the code, no explanation, no markdown fences."
    ),
    "add_comments": (
        "Add clear docstrings and inline comments to this Python file "
        "explaining what each function does. Don't change any logic. "
        "Return only the code, no explanation, no markdown fences."
    ),
}


def strip_markdown_fences(text: str) -> str:
    """LLMs often wrap code in ```python fences despite instructions not to."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:] if lines[0].startswith("```") else lines
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def run_llm_attack(source: str, prompt: str, model: str = "claude-sonnet-4-6") -> str:
    """Send watermarked source to Claude with the given attack prompt, return the result."""
    response = client.messages.create(
        model=model,
        max_tokens=4000,
        messages=[{"role": "user", "content": f"{prompt}\n\n```python\n{source}\n```"}]
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    return strip_markdown_fences(text)


def evaluate_attack(attack_name: str, prompt: str, watermarked_source: str,
                     key: str, payload: list[int]) -> dict:
    """Run one attack, attempt detection, return a structured result."""
    result = {
        "attack": attack_name,
        "llm_output_valid_python": False,
        "carriers_found": 0,
        "detection_succeeded": False,
        "recovered_bits": None,
        "match": False,
        "error": None,
    }

    try:
        attacked_source = run_llm_attack(watermarked_source, prompt)
    except Exception as e:
        result["error"] = f"LLM call failed: {e}"
        return result

    try:
        ast.parse(attacked_source)
        result["llm_output_valid_python"] = True
    except SyntaxError as e:
        result["error"] = f"LLM output is not valid Python: {e}"
        return result

    try:
        tree = ast.parse(attacked_source)
        carriers = find_carriers(tree)
        result["carriers_found"] = len(carriers)

        recovered, found, total = detect(attacked_source, key, payload_len=len(payload))
        result["detection_succeeded"] = True
        result["recovered_bits"] = recovered
        result["match"] = recovered == payload
        print(f"    [debug] recovered={recovered}, expected={payload}, carriers now={found}")
    except ValueError as e:
        result["error"] = f"Detection failed: {e}"

    return result


def main():
    with open("sample_real_2.py") as f:
        original_source = f.read()

    key = "test-secret-key"
    payload = [1, 0, 1, 1, 0, 0, 1, 0]  # matches the 8-bit payload used earlier

    print("Embedding watermark...")
    watermarked_source = embed(original_source, key, payload)
    baseline_recovered, found, total = detect(watermarked_source, key, payload_len=len(payload))
    print(f"Baseline detection (no attack): {baseline_recovered == payload}\n")

    results = []
    for attack_name, prompt in ATTACK_PROMPTS.items():
        print(f"Running attack: {attack_name}...")
        result = evaluate_attack(attack_name, prompt, watermarked_source, key, payload)
        results.append(result)
        status = "SURVIVED" if result["match"] else "FAILED"
        print(f"  -> {status} | carriers: {result['carriers_found']} | error: {result['error']}")
        # save every attacked output for post-hoc analysis
        try:
            attacked = run_llm_attack(watermarked_source, prompt)
            with open(f"debug_{attack_name}_output.py", "w") as f:
                f.write(attacked)
        except Exception:
            pass
        
    print("\n--- Summary ---")
    survived = sum(1 for r in results if r["match"])
    print(f"Watermark survival rate: {survived}/{len(results)} ({100*survived/len(results):.0f}%)")
    for r in results:
        print(f"  {r['attack']:20s} match={r['match']} carriers_found={r['carriers_found']}")

    return results


if __name__ == "__main__":
    main()