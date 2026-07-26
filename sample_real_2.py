"""
Runs two prompts (baseline and candidate) against the golden test set.
Scores each result across schema validity, sentiment accuracy, and urgency accuracy.
Stores all results in SQLite and prints a comparison report.

Usage:
    python runner/eval_runner.py \
        --baseline prompts/crm_summary_v1.yaml \
        --candidate prompts/crm_summary_v2.yaml \
        --test-set evals/golden_set.jsonl
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
import yaml

import anthropic
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from evals.schemas.crm_output_schema import CRMSummary
from pydantic import ValidationError
from storage.init_db import init_db, DB_PATH

load_dotenv()

# Anthropic pricing for claude-haiku-4-5 (USD per token)
COST_PER_INPUT_TOKEN = 0.00000080
COST_PER_OUTPUT_TOKEN = 0.00000400


def load_prompt(path: str) -> dict:
    """Loads a prompt YAML file and returns it as a dictionary."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_test_cases(path: str) -> list:
    """Loads the golden test set from a JSONL file."""
    cases = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def clean_output(raw_text: str) -> str:
    """Strips markdown fences from model output if present."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("```", 1)[0]
    return cleaned.strip()


def call_claude(prompt_config: dict, user_input: str) -> dict:
    """Calls the Anthropic API and returns output, tokens, latency, and cost."""
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    few_shot = ""
    for example in prompt_config.get("few_shot_examples", []):
        few_shot += f"\nInput: {example['input']}\nOutput: {example['output']}\n"

    system = prompt_config["system_prompt"]
    if few_shot:
        system += f"\n\nExamples:{few_shot}"

    start = time.time()
    try:
        response = client.messages.create(
            model=prompt_config["model"]["model_id"],
            max_tokens=prompt_config["model"]["max_tokens"],
            temperature=prompt_config["model"]["temperature"],
            system=system,
            messages=[{"role": "user", "content": f"Customer note: {user_input}"}],
        )
        latency_ms = (time.time() - start) * 1000
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost_usd = (input_tokens * COST_PER_INPUT_TOKEN) + (output_tokens * COST_PER_OUTPUT_TOKEN)

        return {
            "raw_text": response.content[0].text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": round(latency_ms, 2),
            "cost_usd": round(cost_usd, 8),
            "error": None,
        }
    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        return {
            "raw_text": "",
            "input_tokens": 0,
            "output_tokens": 0,
            "latency_ms": round(latency_ms, 2),
            "cost_usd": 0.0,
            "error": str(e),
        }


def score_result(raw_text: str, expected: dict) -> dict:
    """Scores a single model output against the expected values."""
    if not raw_text:
        return {
            "schema_valid": 0,
            "sentiment_correct": 0,
            "urgency_correct": 0,
            "parsed_sentiment": None,
            "parsed_urgency": None,
            "parse_error": "Empty response",
        }

    try:
        cleaned = clean_output(raw_text)
        parsed = json.loads(cleaned)
        validated = CRMSummary(**parsed)

        sentiment_correct = int(validated.sentiment == expected.get("sentiment"))
        urgency_correct = int(validated.urgency == expected.get("urgency"))

        return {
            "schema_valid": 1,
            "sentiment_correct": sentiment_correct,
            "urgency_correct": urgency_correct,
            "parsed_sentiment": validated.sentiment,
            "parsed_urgency": validated.urgency,
            "parse_error": None,
        }
    except (json.JSONDecodeError, ValidationError, Exception) as e:
        return {
            "schema_valid": 0,
            "sentiment_correct": 0,
            "urgency_correct": 0,
            "parsed_sentiment": None,
            "parsed_urgency": None,
            "parse_error": str(e),
        }


def save_result(conn: sqlite3.Connection, run_id: str, prompt_version: str,
                run_type: str, case_id: str, api_result: dict, score: dict):
    """Saves a single eval result row to the database."""
    conn.execute("""
        INSERT INTO eval_runs (
            run_id, prompt_version, run_type, case_id,
            schema_valid, sentiment_correct, urgency_correct,
            latency_ms, cost_usd, input_tokens, output_tokens,
            raw_output, parsed_sentiment, parsed_urgency, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        run_id, prompt_version, run_type, case_id,
        score["schema_valid"], score["sentiment_correct"], score["urgency_correct"],
        api_result["latency_ms"], api_result["cost_usd"],
        api_result["input_tokens"], api_result["output_tokens"],
        api_result["raw_text"], score["parsed_sentiment"], score["parsed_urgency"],
        api_result["error"] or score.get("parse_error"),
    ))


def run_prompt_against_set(prompt_config: dict, cases: list, run_id: str,
                            run_type: str, conn: sqlite3.Connection,
                            quiet: bool = False) -> list:
    """Runs a single prompt against all test cases and saves results."""
    version = prompt_config.get("version", "unknown")
    results = []

    if not quiet:
        print(f"\n  Running {run_type} (v{version}) against {len(cases)} cases...")

    for i, case in enumerate(cases):
        api_result = call_claude(prompt_config, case["input"])
        score = score_result(api_result["raw_text"], case["expected"])
        save_result(conn, run_id, version, run_type, case["id"], api_result, score)
        results.append(score)

        if not quiet:
            status = "PASS" if score["schema_valid"] else "FAIL"
            print(f"  [{i+1:02d}/30] {case['id']} — schema:{status} "
                  f"sentiment:{'OK' if score['sentiment_correct'] else 'MISS'} "
                  f"urgency:{'OK' if score['urgency_correct'] else 'MISS'} "
                  f"({api_result['latency_ms']}ms)")

    conn.commit()
    return results


def compute_summary(results: list) -> dict:
    """Computes aggregate metrics from a list of scored results."""
    total = len(results)
    return {
        "total": total,
        "schema_valid_pct": round(sum(r["schema_valid"] for r in results) / total * 100, 1),
        "sentiment_acc_pct": round(sum(r["sentiment_correct"] for r in results) / total * 100, 1),
        "urgency_acc_pct": round(sum(r["urgency_correct"] for r in results) / total * 100, 1),
    }


def print_report(baseline_summary: dict, candidate_summary: dict,
                 baseline_version: str, candidate_version: str):
    """Prints a comparison report to the console."""
    print("\n" + "=" * 65)
    print("  PROMPTGUARD — REGRESSION REPORT")
    print("=" * 65)
    print(f"  Baseline  : v{baseline_version}    Candidate : v{candidate_version}")
    print("-" * 65)
    print(f"  {'Metric':<25} {'Baseline':>10} {'Candidate':>10} {'Delta':>10}")
    print("-" * 65)

    metrics = [
        ("Schema Validity", "schema_valid_pct", "%"),
        ("Sentiment Accuracy", "sentiment_acc_pct", "%"),
        ("Urgency Accuracy", "urgency_acc_pct", "%"),
    ]

    decisions = []
    for label, key, unit in metrics:
        b = baseline_summary[key]
        c = candidate_summary[key]
        delta = round(c - b, 1)
        sign = "+" if delta >= 0 else ""
        flag = ""
        if delta < -5:
            flag = "  BLOCK"
            decisions.append("BLOCK")
        elif delta < -2:
            flag = "  WARN"
            decisions.append("WARN")
        else:
            decisions.append("PASS")
        print(f"  {label:<25} {b:>9}{unit} {c:>9}{unit} {sign}{delta:>8}{unit}{flag}")

    print("-" * 65)
    if "BLOCK" in decisions:
        final = "BLOCKED"
    elif "WARN" in decisions:
        final = "WARNING — review before merging"
    else:
        final = "PASSED — safe to merge"

    print(f"\n  Decision: {final}")
    print("=" * 65 + "\n")


def main():
    parser = argparse.ArgumentParser(description="PromptGuard regression runner")
    parser.add_argument("--baseline", required=True, help="Path to baseline prompt YAML")
    parser.add_argument("--candidate", required=True, help="Path to candidate prompt YAML")
    parser.add_argument("--test-set", required=True, help="Path to golden set JSONL")
    parser.add_argument("--ci-mode", action="store_true", help="Suppress per-case output for CI logs")
    args = parser.parse_args()

    init_db()

    baseline_config = load_prompt(args.baseline)
    candidate_config = load_prompt(args.candidate)
    cases = load_test_cases(args.test_set)

    run_id = str(uuid.uuid4())[:8]
    print(f"\nPromptGuard run ID: {run_id}")
    print(f"Test cases loaded : {len(cases)}")

    # Write run_id to file for reliable CI capture
    with open("run_id.txt", "w") as f:
        f.write(run_id)
    
    conn = sqlite3.connect(DB_PATH)

    baseline_results = run_prompt_against_set(
        baseline_config, cases, run_id, "baseline", conn, quiet=args.ci_mode
    )
    candidate_results = run_prompt_against_set(
        candidate_config, cases, run_id, "candidate", conn, quiet=args.ci_mode
    )

    conn.close()

    baseline_summary = compute_summary(baseline_results)
    candidate_summary = compute_summary(candidate_results)

    print_report(
        baseline_summary, candidate_summary,
        baseline_config.get("version", "?"),
        candidate_config.get("version", "?"),
    )


if __name__ == "__main__":
    main()

