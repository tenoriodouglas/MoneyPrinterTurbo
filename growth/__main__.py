"""Command line for the niche layer.

    python -m growth niches
    python -m growth plan personal-finance --count 5
    python -m growth produce storage/growth/plans/personal-finance-<stamp>
    python -m growth run ai-tools --count 3
    python -m growth ledger --limit 10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from growth.configure import ConfigError, apply_updates, build_updates, mask
from growth.doctor import FAIL, OK, WARN, run_checks
from growth.niche import NicheError, load_all_niches
from growth.plan import PlanError, create_plan
from growth.produce import LEDGER_PATH, ProduceError, produce
from growth.review import review_all


def _cmd_niches(args: argparse.Namespace) -> int:
    packs = load_all_niches()
    if not packs:
        print("no niche packs found in ./niches", file=sys.stderr)
        return 1
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": n.id,
                        "name": n.name,
                        "cpm": [n.economics.cpm_low, n.economics.cpm_high],
                        "rpm": list(n.economics.rpm_range),
                        "competition": n.economics.competition,
                        "platforms": list(n.platforms),
                        "monetization": n.monetization.get("primary", ""),
                    }
                    for n in packs
                ],
                indent=2,
            )
        )
        return 0

    print(f"{'ID':<20} {'CPM (USD)':<12} {'RPM est.':<14} {'COMPETITION':<12} PRIMARY REVENUE")
    print("-" * 82)
    for n in packs:
        cpm = f"${n.economics.cpm_low:.0f}-${n.economics.cpm_high:.0f}"
        low, high = n.economics.rpm_range
        print(
            f"{n.id:<20} {cpm:<12} ${low:.2f}-${high:.2f}".ljust(48)
            + f"{n.economics.competition:<12} {n.monetization.get('primary', '')}"
        )
    print(
        "\nCPM is what advertisers pay per 1000 monetised views; RPM is your share.\n"
        "Neither applies until the channel is accepted into a partner programme."
    )
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    updates = build_updates(
        pexels=args.pexels,
        pixabay=args.pixabay,
        provider=args.llm,
        provider_key=args.llm_key,
        provider_model=args.llm_model,
    )
    backup = apply_updates(updates)
    print("updated config.toml:")
    for key, value in updates.items():
        # Keys are masked so a terminal recording never captures one.
        shown = mask(value[0]) if isinstance(value, list) else value
        shown = mask(shown) if key.endswith("_api_key") else shown
        print(f"  {key} = {shown}")
    print(f"\nbackup: {backup.name}")
    print("\nverify it:\n  python -m growth doctor")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    results = run_checks(skip_network=args.skip_network)
    marks = {OK: "\033[32m  ok  \033[0m", WARN: "\033[33m warn \033[0m", FAIL: "\033[31m FAIL \033[0m"}
    print()
    for check in results:
        print(f"[{marks[check.status]}] {check.name:<12} {check.detail}")
        if check.fix and check.status != OK:
            print(f"{'':>21}-> {check.fix}")
    failed = [c for c in results if c.status == FAIL]
    warned = [c for c in results if c.status == WARN]
    print()
    if failed:
        print(f"{len(failed)} check(s) failed; fix those before running a batch")
        return 1
    if warned:
        print(f"ready to render, with {len(warned)} warning(s)")
    else:
        print("ready to render:  python -m growth run ai-tools --count 1")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    plan = create_plan(
        args.niche,
        count=args.count,
        seed=args.seed,
        aspect=args.aspect,
        paragraphs=args.paragraphs,
    )
    print(f"planned {plan['count']} videos for {plan['niche_name']}")
    for number, brief in enumerate(plan["briefs"], start=1):
        print(f"  {number}. [{brief['angle'][:28]}] {brief['subject']}")
    print(f"\nplan:     {plan['plan_file']}")
    print(f"manifest: {plan['manifest']}")
    print(f"\nrender it with:\n  python -m growth produce {Path(plan['plan_file']).parent}")
    return 0


def _cmd_produce(args: argparse.Namespace) -> int:
    result = produce(
        Path(args.plan_dir),
        stop_at=args.stop_at,
        timeout=args.timeout,
        quiet=args.quiet,
    )
    print(f"rendered {result['succeeded']}/{result['total']} videos")
    for record in result["records"]:
        mark = "ok  " if record["status"] == "succeeded" else "FAIL"
        detail = record["files"][0] if record["files"] else (record["error"] or "")
        print(f"  [{mark}] {record['subject'][:54]:<54} {detail}")
    if result["failed"]:
        return 1
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    plan = create_plan(
        args.niche,
        count=args.count,
        seed=args.seed,
        aspect=args.aspect,
        paragraphs=args.paragraphs,
    )
    print(f"planned {plan['count']} videos for {plan['niche_name']}")
    result = produce(
        Path(plan["plan_file"]).parent,
        stop_at="video",
        timeout=args.timeout,
        quiet=args.quiet,
    )
    print(f"rendered {result['succeeded']}/{result['total']} videos")
    for record in result["records"]:
        mark = "ok  " if record["status"] == "succeeded" else "FAIL"
        detail = record["files"][0] if record["files"] else (record["error"] or "")
        print(f"  [{mark}] {record['subject'][:54]:<54} {detail}")
    return 1 if result["failed"] else 0


def _cmd_review(args: argparse.Namespace) -> int:
    reviews = review_all(limit=args.limit)
    if not reviews:
        print("nothing produced yet; run `python -m growth run <niche>` first")
        return 0

    marks = {OK: "\033[32m  ok  \033[0m", WARN: "\033[33m warn \033[0m", FAIL: "\033[31m FAIL \033[0m"}
    print()
    for review in reviews:
        print(
            f"[{marks[review.status]}] {review.subject[:50]:<50} "
            f"{review.duration:>5.0f}s  {review.width}x{review.height}  "
            f"{review.words} words"
        )
        for _, message in review.issues:
            print(f"{'':>21}-> {message}")
    failed = [r for r in reviews if r.status == FAIL]
    print()
    if failed:
        print(f"{len(failed)} of {len(reviews)} video(s) should not be published as is")
        return 1
    print(f"all {len(reviews)} video(s) pass; captions are in the same folder")
    return 0


def _cmd_ledger(args: argparse.Namespace) -> int:
    if not LEDGER_PATH.is_file():
        print("no ledger yet; run `python -m growth run <niche>` first")
        return 0
    rows = [
        json.loads(line)
        for line in LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for row in rows[-args.limit :]:
        flag = "published" if row.get("published") else "unposted"
        print(f"[{row.get('status', '?'):<9}] [{flag:<9}] {row.get('niche_id', '')}: {row.get('subject', '')}")
    print(f"\n{len(rows)} videos in ledger: {LEDGER_PATH}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="growth",
        description="Plan and render batches of niche videos on the render engine.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser(
        "config", help="set api keys in config.toml without editing it by hand"
    )
    configure.add_argument("--pexels", help="pexels api key")
    configure.add_argument("--pixabay", help="pixabay api key")
    configure.add_argument("--llm", help="llm provider id, e.g. gemini")
    configure.add_argument("--llm-key", help="api key for the provider given by --llm")
    configure.add_argument(
        "--llm-model",
        help="explicit model name; needed when the provider default is not on your plan",
    )
    configure.set_defaults(func=_cmd_config)

    doctor = subparsers.add_parser(
        "doctor", help="check this machine and config before rendering"
    )
    doctor.add_argument(
        "--skip-network",
        action="store_true",
        help="only run local checks, no provider calls",
    )
    doctor.set_defaults(func=_cmd_doctor)

    listing = subparsers.add_parser("niches", help="list niche packs by earning profile")
    listing.add_argument("--json", action="store_true", help="machine readable output")
    listing.set_defaults(func=_cmd_niches)

    planning = subparsers.add_parser("plan", help="generate briefs and a batch manifest")
    planning.add_argument("niche", help="niche pack id, see `growth niches`")
    planning.add_argument("--count", type=int, default=5, help="number of videos")
    planning.add_argument("--seed", type=int, default=None, help="reproducible variation")
    planning.add_argument(
        "--aspect",
        choices=["9:16", "16:9", "1:1"],
        default=None,
        help="override the pack default; 16:9 is the long-form cut",
    )
    planning.add_argument(
        "--paragraphs",
        type=int,
        default=None,
        help="script length in paragraphs, 1-10; raise it for long-form",
    )
    planning.set_defaults(func=_cmd_plan)

    producing = subparsers.add_parser("produce", help="render an existing plan directory")
    producing.add_argument("plan_dir", help="directory containing plan.json")
    producing.add_argument(
        "--stop-at",
        default="video",
        choices=["script", "terms", "audio", "subtitle", "materials", "video"],
        help="stop the pipeline early, for dry runs",
    )
    producing.add_argument("--timeout", type=int, default=6 * 60 * 60)
    producing.add_argument(
        "--quiet", action="store_true", help="hide the engine's render log"
    )
    producing.set_defaults(func=_cmd_produce)

    running = subparsers.add_parser("run", help="plan and render in one step")
    running.add_argument("niche")
    running.add_argument("--count", type=int, default=3)
    running.add_argument("--seed", type=int, default=None)
    running.add_argument("--aspect", choices=["9:16", "16:9", "1:1"], default=None)
    running.add_argument("--paragraphs", type=int, default=None)
    running.add_argument("--timeout", type=int, default=6 * 60 * 60)
    running.add_argument(
        "--quiet", action="store_true", help="hide the engine's render log"
    )
    running.set_defaults(func=_cmd_run)

    reviewer = subparsers.add_parser(
        "review", help="check produced videos against the rules that let them earn"
    )
    reviewer.add_argument(
        "--limit", type=int, default=10, help="how many recent videos to check"
    )
    reviewer.set_defaults(func=_cmd_review)

    ledger = subparsers.add_parser("ledger", help="show what has been produced")
    ledger.add_argument("--limit", type=int, default=20)
    ledger.set_defaults(func=_cmd_ledger)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (NicheError, PlanError, ProduceError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        # Piping into head/less closes stdout early; that is not a failure.
        sys.stdout = None
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
