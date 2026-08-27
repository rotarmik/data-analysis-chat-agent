"""Offline evaluation harness.

For each golden question the agent is run end-to-end, then the answer is
scored two ways:
1. Deterministic checks — PII leak scan, non-empty answer, error markers.
2. LLM-as-judge — does the answer satisfy the stated intent (1-5)?

Usage: python -m evals.run_evals [--only id1,id2]
Writes evals/results.md.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from src.agent import build_agent
from src.config import settings
from src.llm import build_chat_model
from src.observability import build_langfuse

EVALS_DIR = Path(__file__).resolve().parent
LANGFUSE_HANDLER, LANGFUSE = build_langfuse()
PASS_THRESHOLD = 4

JUDGE_PROMPT = """\
You are a strict evaluator of a data-analysis assistant for retail executives.

Question asked by the user:
{question}

What a correct answer must contain:
{expect}

Assistant's answer:
---
{answer}
---

Score how well the answer satisfies the requirement on a 1-5 scale:
5 = fully satisfies intent, numbers are present and coherent
4 = satisfies intent with minor omissions
3 = partially useful, misses a stated requirement
2 = mostly misses the intent
1 = wrong, empty, evasive or fabricated

Reply with JSON only: {{"score": <int>, "reason": "<one sentence>"}}"""


def run_agent(question: str, case_id: str) -> str:
    agent = build_agent("eval_user", settings.default_persona, InMemorySaver())
    config = {
        "configurable": {"thread_id": "eval"},
        "recursion_limit": settings.recursion_limit,
        "run_name": f"eval:{case_id}",
    }
    if LANGFUSE_HANDLER:
        config["callbacks"] = [LANGFUSE_HANDLER]
        config["metadata"] = {"langfuse_tags": ["eval", case_id], "langfuse_user_id": "eval_user"}
    result = agent.invoke({"messages": [HumanMessage(question)]}, config=config)
    for message in reversed(result["messages"]):
        if isinstance(message, AIMessage) and str(message.content).strip():
            return str(message.content)
    return ""


def deterministic_checks(case: dict, answer: str) -> list[str]:
    failures = []
    if not answer.strip():
        failures.append("empty answer")
    if re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", answer):
        failures.append("PII leak: email present in answer")
    if "SQL_ERROR" in answer or "Traceback" in answer:
        failures.append("raw error leaked to user")
    return failures


def judge(case: dict, answer: str) -> dict:
    model = build_chat_model()
    response = model.invoke(
        JUDGE_PROMPT.format(question=case["question"], expect=case["expect"], answer=answer[:6000])
    )
    text = str(response.content)
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return {"score": 0, "reason": f"judge returned unparseable output: {text[:100]}"}
    return json.loads(match.group(0))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated case ids to run")
    args = parser.parse_args()

    cases = json.loads((EVALS_DIR / "questions.json").read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    rows, passed = [], 0
    for case in cases:
        print(f"[eval] {case['id']} ...", flush=True)
        try:
            answer = run_agent(case["question"], case["id"])
        except Exception as exc:
            rows.append((case["id"], 0, f"agent crashed: {exc}", ""))
            continue
        failures = deterministic_checks(case, answer)
        verdict = judge(case, answer)
        ok = not failures and verdict["score"] >= PASS_THRESHOLD
        passed += ok
        status = "PASS" if ok else "FAIL"
        notes = "; ".join(failures) if failures else verdict["reason"]
        print(f"  {status} score={verdict['score']} {notes}")
        rows.append((case["id"], verdict["score"], notes, answer))

    report = ["# Evaluation results\n", f"Passed: {passed}/{len(cases)} (judge score >= {PASS_THRESHOLD}, no deterministic failures)\n"]
    report.append("| case | score | notes |")
    report.append("|---|---|---|")
    for case_id, score, notes, _ in rows:
        report.append(f"| {case_id} | {score} | {notes.replace('|', '/')} |")
    report.append("\n## Answers\n")
    for case_id, _, _, answer in rows:
        report.append(f"### {case_id}\n\n{answer}\n")
    (EVALS_DIR / "results.md").write_text("\n".join(report), encoding="utf-8")
    print(f"\nPassed {passed}/{len(cases)}. Full report: evals/results.md")
    if LANGFUSE:
        LANGFUSE.flush()
    sys.exit(0 if passed == len(cases) else 1)


if __name__ == "__main__":
    main()
