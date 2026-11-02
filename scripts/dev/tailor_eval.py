#!/usr/bin/env python3
"""Run Normal mode's real prompt over the 20 synthetic postings in tests/tailoring_eval_data.py and write what came back, for reading.

    EVAL_LLM_API_KEY=... python scripts/dev/tailor_eval.py --out results.json [--limit 20] [--model deepseek/deepseek-v4.1-flash-1017]
    python scripts/dev/tailor_eval.py --out results.json --stub sloppy      A model that misses limits by a little and sometimes lies. Free.
    python scripts/dev/tailor_eval.py --out results.json --stub lying       A model that lies on every line. Free.

A real model costs real money, at about 0.1 cents a posting. The key comes only from the environment and is never printed. Everything is written to a throwaway database in a temporary
folder, so no installed app is touched. Two checks run on each result: claims.py and an independent read that shares no code with it. Read the diffs yourself too.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--model", default="deepseek/deepseek-v4.1-flash-1017")
    parser.add_argument("--stub", choices=["lying", "sloppy"], help="Use a stand-in for the model: no key, no cost.")
    args = parser.parse_args()
    out_path = Path(args.out).resolve()  # It works in a temporary folder.
    key = os.environ.pop("EVAL_LLM_API_KEY", "")
    if not key and not args.stub:
        sys.exit("Set EVAL_LLM_API_KEY, or pass --stub. Nothing was run.")
    from tailoring_eval_data import POSTINGS, RESUMES, profile_of
    from test_tailoring_evals import hostile, independent_read, sloppy

    from jobpilot.config import Settings
    from jobpilot.db import Database
    from jobpilot.features._store import create_tables
    from jobpilot.features.knowledge.writing import configured_model
    from jobpilot.features.resumes import ats, claims, editor, modes, normal
    from jobpilot.features.resumes.compiler import compile_pdf
    from jobpilot.features.resumes.evidence import build
    from jobpilot.features.resumes.rules import check_rules, doc_plain
    from jobpilot.features.resumes.select import supported_terms
    from jobpilot.models import COST_METER

    rows = []
    with tempfile.TemporaryDirectory(prefix="ax-eval-") as work:
        os.chdir(work)
        config = Settings(
            _env_file=None, database_url="", dbos_database_url="", data_dir=Path(work), app_token="eval", llm_provider="openrouter", openrouter_api_key=key or "stub", openrouter_model=args.model, enable_workers=False, telegram_enabled=False
        ).prepare()
        db = Database(config.database_url)
        create_tables(db.engine)
        real = configured_model(db, config)
        for posting in POSTINGS[: args.limit]:
            model = {"lying": hostile, "sloppy": sloppy}[args.stub](posting) if args.stub else real
            doc = RESUMES[posting["resume"]]
            profile = profile_of(doc)
            inv = build(profile, doc_plain(doc))
            terms = ats.terms(posting["description"], posting["title"], supported_terms(inv), company=posting["company"])
            evidence_text = " ".join([doc_plain(doc), inv.vocabulary])
            source = modes.SourceResume(id="r", sha="s", text=doc_plain(doc), doc=doc, style=editor.Style())
            start = ats.score(terms, doc_plain(doc), evidence_text)
            meter: list = []
            token = COST_METER.set(meter)
            row = {"resume": posting["resume"], "title": posting["title"]}
            try:
                ctx = modes.ModeContext(db=db, config=config, model=model, compile_fn=compile_pdf, work_dir=Path(work), evidence=inv, ats_terms=terms, ats_source=start)
                result = await normal.run(posting, source, profile, ctx)
            except Exception as exc:  # Its type only: a provider's error can quote the request.
                row["error"] = type(exc).__name__
                rows.append(row)
                continue
            finally:
                COST_METER.reset(token)
            pdf = result.pdf_path.read_bytes()
            report = check_rules(result.doc, doc, pdf=pdf, allowed_skills=[*inv.skills, *result.allowed_skills], vocabulary=inv.vocabulary)
            originals = {b.id: b.text for s in doc.sections for e in s.entries for b in e.bullets}
            changes, flagged = [], []
            for s in result.doc.sections:
                for e in s.entries:
                    for b in e.bullets:
                        if b.id in originals and b.text != originals[b.id]:
                            changes.append({"from": originals[b.id], "to": b.text})
                            own = " ".join(x.text for x in e.bullets) + " " + e.heading + " " + e.subheading
                            flagged += [p for p in claims.check(b.text, originals[b.id], own) if p.startswith("unsupported")]
            summary = next((s.text for s in result.doc.sections if s.title == "Summary"), "")
            row.update(
                rules_ok=report["ok"],
                problems=[p["message"] for p in report["problems"]],
                claims_flagged=flagged,
                independent_read=independent_read(doc, result.doc, profile, posting),
                score_source=start["score"],
                score=ats.score(terms, doc_plain(result.doc), evidence_text)["score"],
                needed=[k["term"] for k in start["keywords"] if k["status"] == "needed"],
                summary=summary,
                summary_origin=result.report["summary_origin"],
                changes=changes,
                offered=result.report["offered_bullets"],
                repaired=result.report["repaired_bullets"],
                added_skills=result.report["added_skills"],
                refused=result.report["refused"],
                pages=report["pages"],
                calls=len(meter),
                usd=round(sum(m["usd"] for m in meter), 6),
                prompt_tokens=sum(m["prompt_tokens"] or 0 for m in meter),
                completion_tokens=sum(m["completion_tokens"] or 0 for m in meter),
            )
            rows.append(row)
            print(f"{row['resume']:>10} {row['title'][:30]:<30} {row['score_source']}->{row['score']} offered {row['offered']} changed {len(changes)} repaired {row['repaired']} refused {len(row['refused'])} flags {len(flagged)}+{len(row['independent_read'])} {row['usd'] * 100:.3f}c", flush=True)
    out_path.write_text(json.dumps(rows, indent=1))
    done = [r for r in rows if "error" not in r]
    usd = [r["usd"] for r in done]
    print(f"Offered {sum(r['offered'] for r in done)} bullets, changed {sum(len(r['changes']) for r in done)}, repaired {sum(r['repaired'] for r in done)}. Score rose on {sum(r['score'] > r['score_source'] for r in done if r['score'] is not None)} postings.")
    print(f"{len(done)}/{len(rows)} ran. Invented, by claims.py: {sum(len(r['claims_flagged']) for r in done)}; by the independent read: {sum(len(r['independent_read']) for r in done)}. Rules failed: {sum(not r['rules_ok'] for r in done)}.")
    if usd:
        print(f"Cost per tailoring: mean {sum(usd) / len(usd) * 100:.3f} cents, highest {max(usd) * 100:.3f} cents.")


if __name__ == "__main__":
    asyncio.run(main())
