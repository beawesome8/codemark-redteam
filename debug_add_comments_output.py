"""
Runs two prompts (baseline and candidate) against the golden test set.
Scores each result across schema validity, sentiment accuracy, and urgency accuracy.
Stores all results in SQLite and prints a comparison report.

Usage:
    python runner/eval_runner.py         --baseline prompts/crm_summary_v1.yaml         --candidate prompts/crm_summary_v2.yaml         --test-set evals/golden_set.jsonl
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

# Per-token pricing constants for the Anthropic API
COST_PER_INPUT_TOKEN = 8e-07   # Cost in USD for each input (prompt) token
COST_PER_OUTPUT_TOKEN = 4e-06  # Cost in USD for each output (completion) token


def load_prompt(path: str) -> dict:
    """Load a prompt configuration from a YAML file.

    Args:
        path: Filesystem path to the YAML prompt configuration file.

    Returns:
        A dictionary containing the prompt configuration, including keys such as
        'system_prompt', 'model', 'few_shot_examples', and 'version'.
    """
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def load_test_cases(path: str) -> list:
    """Load golden test cases from a JSONL file.

    Each line in the file is expected to be a valid JSON object representing
    one test case, with at minimum an 'id', 'input', and 'expected' field.

    Args:
        path: Filesystem path to the JSONL golden test set file.

    Returns:
        A list of dictionaries, one per test case.
    """
    cases = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:  # Skip blank lines that may appear between records
                cases.append(json.loads(line))
    return cases


def clean_output(raw_text: str) -> str:
    """Strip markdown code fences from a model response string.

    Some model outputs wrap JSON in triple-backtick fences (e.g. ```json ... ```).
    This function removes those fences so the inner content can be parsed as JSON.

    Args:
        raw_text: The raw string returned by the model.

    Returns:
        The cleaned string with leading/trailing fences and whitespace removed.
    """
    cleaned = raw_text.strip()

    # Remove opening fence line (e.g. "```json\n" or "```\n")
    if cleaned.startswith('```'):
        cleaned = cleaned.split('\n', 1)[1]

    # Remove closing fence (e.g. "\n```")
    if cleaned.endswith('```'):
        cleaned = cleaned.rsplit('```', 1)[0]

    return cleaned.strip()


def call_claude(prompt_config: dict, user_input: str) -> dict:
    """Send a prompt and user input to the Anthropic API and return structured results.

    Builds the system prompt by appending any configured few-shot examples,
    then calls the Claude model specified in the prompt configuration. Captures
    token usage, latency, and estimated cost.

    Args:
        prompt_config: Dictionary loaded from a prompt YAML file, containing
                       'system_prompt', 'model' (with 'model_id', 'max_tokens',
                       'temperature'), and optionally 'few_shot_examples'.
        user_input: The raw customer note text to send as the user message.

    Returns:
        A dictionary with the following keys:
            - 'raw_text'      : The model's response text (str).
            - 'input_tokens'  : Number of input tokens consumed (int).
            - 'output_tokens' : Number of output tokens generated (int).
            - 'latency_ms'    : Round-trip latency in milliseconds (float).
            - 'cost_usd'      : Estimated cost of the call in USD (float).
            - 'error'         : Error message string if the call failed, else None.
    """
    client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))

    # Build the few-shot block by concatenating all example input/output pairs
    few_shot = ''
    for example in prompt_config.get('few_shot_examples', list()):
        few_shot += f'\nInput: {example['input']}\nOutput: {example['output']}\n'

    # Append examples to the system prompt when they are present
    system = prompt_config['system_prompt']
    if few_shot:
        system += f'\n\nExamples:{few_shot}'

    start = time.time()
    try:
        response = client.messages.create(
            model=prompt_config['model']['model_id'],
            max_tokens=prompt_config['model']['max_tokens'],
            temperature=prompt_config['model']['temperature'],
            system=system,
            messages=[{'role': 'user', 'content': f'Customer note: {user_input}'}]
        )

        # Calculate wall-clock latency from request start to response received
        latency_ms = (time.time() - start) * 1000

        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens

        # Estimate monetary cost based on token counts and fixed per-token rates
        cost_usd = input_tokens * COST_PER_INPUT_TOKEN + output_tokens * COST_PER_OUTPUT_TOKEN

        return {
            'raw_text': response.content[0].text,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'latency_ms': round(latency_ms, 2),
            'cost_usd': round(cost_usd, 8),
            'error': None
        }
    except Exception as e:
        # On any API failure, capture latency and return zeroed metrics with the error
        latency_ms = (time.time() - start) * 1000
        return {
            'raw_text': '',
            'input_tokens': 0,
            'output_tokens': 0,
            'latency_ms': round(latency_ms, 2),
            'cost_usd': 0.0,
            'error': str(e)
        }


def score_result(raw_text: str, expected: dict) -> dict:
    """Score a single model output against the expected ground-truth values.

    Attempts to clean, parse, and validate the model's raw text output as a
    CRMSummary object, then compares the extracted sentiment and urgency fields
    against the expected values from the golden test set.

    Args:
        raw_text: The raw string output returned by the model.
        expected: Dictionary of ground-truth values, expected to contain
                  'sentiment' and 'urgency' keys.

    Returns:
        A dictionary with the following keys:
            - 'schema_valid'      : 1 if output parsed and validated, else 0 (int).
            - 'sentiment_correct' : 1 if predicted sentiment matches expected, else 0 (int).
            - 'urgency_correct'   : 1 if predicted urgency matches expected, else 0 (int).
            - 'parsed_sentiment'  : The sentiment string extracted from output, or None.
            - 'parsed_urgency'    : The urgency string extracted from output, or None.
            - 'parse_error'       : Error message string if parsing failed, else None.
    """
    # Guard against empty API responses before attempting any parsing
    if not raw_text:
        return {
            'schema_valid': 0,
            'sentiment_correct': 0,
            'urgency_correct': 0,
            'parsed_sentiment': None,
            'parsed_urgency': None,
            'parse_error': 'Empty response'
        }

    try:
        # Remove markdown fences, then parse JSON and validate against the CRM schema
        cleaned = clean_output(raw_text)
        parsed = json.loads(cleaned)
        validated = CRMSummary(**parsed)

        # Compare each predicted field to its expected ground-truth value
        sentiment_correct = int(validated.sentiment == expected.get('sentiment'))
        urgency_correct = int(validated.urgency == expected.get('urgency'))

        return {
            'schema_valid': 1,
            'sentiment_correct': sentiment_correct,
            'urgency_correct': urgency_correct,
            'parsed_sentiment': validated.sentiment,
            'parsed_urgency': validated.urgency,
            'parse_error': None
        }
    except (json.JSONDecodeError, ValidationError, Exception) as e:
        # Any parsing or validation failure is treated as a fully failed result
        return {
            'schema_valid': 0,
            'sentiment_correct': 0,
            'urgency_correct': 0,
            'parsed_sentiment': None,
            'parsed_urgency': None,
            'parse_error': str(e)
        }


def save_result(conn: sqlite3.Connection, run_id: str, prompt_version: str, run_type: str, case_id: str, api_result: dict, score: dict):
    """Persist a single evaluation result row to the SQLite database.

    Writes all API call metrics and scoring outcomes for one test case into the
    'eval_runs' table. The error column is populated with either an API error or
    a parse error, whichever is present.

    Args:
        conn:           An open SQLite database connection.
        run_id:         Short unique identifier for the current evaluation run.
        prompt_version: Version string from the prompt YAML (e.g. '1.2').
        run_type:       Either 'baseline' or 'candidate' indicating which prompt was used.
        case_id:        The identifier of the test case from the golden set.
        api_result:     Dictionary returned by call_claude() with token and latency data.
        score:          Dictionary returned by score_result() with validity and accuracy flags.
    """
    conn.execute(
        '\n        INSERT INTO eval_runs (\n            run_id, prompt_version, run_type, case_id,\n            schema_valid, sentiment_correct, urgency_correct,\n            latency_ms, cost_usd, input_tokens, output_tokens,\n            raw_output, parsed_sentiment, parsed_urgency, error\n        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n    ',
        (
            run_id, prompt_version, run_type, case_id,
            score['schema_valid'], score['sentiment_correct'], score['urgency_correct'],
            api_result['latency_ms'], api_result['cost_usd'],
            api_result['input_tokens'], api_result['output_tokens'],
            api_result['raw_text'],
            score['parsed_sentiment'], score['parsed_urgency'],
            # Prefer the API-level error; fall back to a parse-level error if present
            api_result['error'] or score.get('parse_error')
        )
    )


def run_prompt_against_set(prompt_config: dict, cases: list, run_id: str, run_type: str, conn: sqlite3.Connection, quiet: bool=False) -> list:
    """Execute a prompt configuration against all test cases and store the results.

    Iterates over every test case, calls the Claude API, scores the response, and
    saves each result to the database. Optionally prints per-case progress output.

    Args:
        prompt_config: Dictionary loaded from a prompt YAML file.
        cases:         List of test case dictionaries from the golden set.
        run_id:        Unique identifier for the current evaluation run.
        run_type:      Label string, either 'baseline' or 'candidate'.
        conn:          An open SQLite database connection.
        quiet:         When True, suppresses per-case console output (useful in CI).

    Returns:
        A list of score dictionaries (one per test case) as returned by score_result().
    """
    version = prompt_config.get('version', 'unknown')
    results = []

    if not quiet:
        print(f'\n  Running {run_type} (v{version}) against {len(cases)} cases...')

    for i, case in enumerate(cases):
        # Call the API and score the response for this individual test case
        api_result = call_claude(prompt_config, case['input'])
        score = score_result(api_result['raw_text'], case['expected'])

        # Persist the result immediately so a partial run is still recoverable
        save_result(conn, run_id, version, run_type, case['id'], api_result, score)
        results.append(score)

        if not quiet:
            # Summarise pass/fail status for each scored dimension on one line
            status = 'PASS' if score['schema_valid'] else 'FAIL'
            print(f'  [{i + 1:02d}/30] {case['id']} â€" schema:{status} sentiment:{('OK' if score['sentiment_correct'] else 'MISS')} urgency:{('OK' if score['urgency_correct'] else 'MISS')} ({api_result['latency_ms']}ms)')

    # Commit all inserts for this prompt run as a single transaction
    conn.commit()
    return results


def compute_summary(results: list) -> dict:
    """Compute aggregate accuracy percentages from a list of per-case scores.

    Args:
        results: List of score dictionaries as returned by score_result(),
                 one entry per evaluated test case.

    Returns:
        A dictionary with the following keys:
            - 'total'              : Total number of test cases evaluated (int).
            - 'schema_valid_pct'   : Percentage of cases with valid schema output (float).
            - 'sentiment_acc_pct'  : Percentage of cases with correct sentiment (float).
            - 'urgency_acc_pct'    : Percentage of cases with correct urgency (float).
    """
    total = len(results)
    return {
        'total': total,
        # Sum binary flags across all cases, then convert to a 0–100 percentage
        'schema_valid_pct': round(sum((r['schema_valid'] for r in results)) / total * 100, 1),
        'sentiment_acc_pct': round(sum((r['sentiment_correct'] for r in results)) / total * 100, 1),
        'urgency_acc_pct': round(sum((r['urgency_correct'] for r in results)) / total * 100, 1)
    }


def print_report(baseline_summary: dict, candidate_summary: dict, baseline_version: str, candidate_version: str):
    """Print a formatted side-by-side regression comparison report to stdout.

    For each metric, displays the baseline value, candidate value, and delta.
    Flags regressions with WARN (delta < -2 %) or BLOCK (delta < -5 %), and
    derives an overall go/no-go decision from the worst flag encountered.

    Args:
        baseline_summary:  Summary dict from compute_summary() for the baseline prompt.
        candidate_summary: Summary dict from compute_summary() for the candidate prompt.
        baseline_version:  Version label string for the baseline prompt.
        candidate_version: Version label string for the candidate prompt.
    """
    print('\n' + '=' * 65)
    print('  PROMPTGUARD â€" REGRESSION REPORT')
    print('=' * 65)
    print(f'  Baseline  : v{baseline