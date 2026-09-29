"""Run the copilot eval dataset (backend/app/explain/evals.py).

    python scripts/copilot_eval.py              # local: templates, or the LLM when OPENAI_API_KEY is set
    python scripts/copilot_eval.py --langsmith  # also upload the dataset and record an experiment in LangSmith
                                                # (needs LANGSMITH_API_KEY; LANGSMITH_PROJECT names the project)

Exit code 1 if any case fails, so it can gate CI once an LLM key is available there.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

from app.explain.evals import check, load_cases, run_case  # noqa: E402
from app.explain.service import Explainer  # noqa: E402

DATASET = "fuelguard-copilot-faithfulness"


def local() -> int:
    ex = Explainer()
    results = [run_case(c, ex) for c in load_cases()]
    for r in results:
        print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['id']:<24} {r['source']:<8} {'; '.join(r['problems'])}")
    failed = sum(not r["passed"] for r in results)
    print(f"\n{len(results) - failed}/{len(results)} passed ({'LLM' if ex.llm_enabled else 'templates only'})")
    return 1 if failed else 0


def langsmith() -> int:
    from langsmith import Client
    from langsmith.evaluation import evaluate

    cases = {c["id"]: c for c in load_cases()}
    client = Client()
    if not client.has_dataset(dataset_name=DATASET):
        ds = client.create_dataset(DATASET, description="FuelGuard copilot: facts in, faithful explanation out")
        client.create_examples(dataset_id=ds.id, inputs=[{"case_id": k, "facts": c["facts"], "question": c["question"]}
                                                         for k, c in cases.items()],
                               outputs=[{"key_numbers": c.get("key_numbers", [])} for c in cases.values()])
    ex = Explainer()

    def target(inputs: dict) -> dict:
        out = ex.run(inputs["facts"], inputs.get("question"))
        return {"text": out.text, "source": out.source}

    def faithful(run, example) -> dict:
        case = cases[example.inputs["case_id"]]
        res = check(run.outputs["text"], case)
        return {"key": "faithful", "score": int(res["passed"]), "comment": "; ".join(res["problems"])}

    evaluate(target, data=DATASET, evaluators=[faithful], experiment_prefix="copilot",
             metadata={"llm": os.getenv("COPILOT_MODEL", "gpt-4o-mini") if ex.llm_enabled else "template"})
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--langsmith", action="store_true")
    code = local()
    if p.parse_args().langsmith:
        code = langsmith() or code
    sys.exit(code)
