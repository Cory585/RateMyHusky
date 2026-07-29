"""Chain the existing prod-publish scripts: backup -> precompute -> evidence -> embed.
The ingest->finalize gap is the verification checkpoint: precompute is the moment data goes live.

Usage:  python scraper/trace_pipeline/finalize.py [--skip-backup] [--from STEP]
"""
import argparse, os, subprocess, sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STEP_ORDER = ["backup", "precompute", "evidence", "embed"]

def build_steps(skip_backup, from_step):
    py = sys.executable
    all_steps = [
        ("backup",     [py, os.path.join(_REPO_ROOT, "backend", "backup_db.py")]),
        ("precompute", [py, os.path.join(_REPO_ROOT, "backend", "precompute.py")]),
        ("evidence",   [py, os.path.join(_REPO_ROOT, "scraper", "load_evidence_to_crdb.py"), "--build-evidence"]),
        ("embed",      [py, os.path.join(_REPO_ROOT, "scraper", "embed_evidence.py"), "--embed"]),
    ]
    start = STEP_ORDER.index(from_step) if from_step else 0
    steps = all_steps[start:]
    if skip_backup:
        steps = [(n, a) for n, a in steps if n != "backup"]
    return steps

def run_steps(steps, runner):
    for name, argv in steps:
        print(f"\n{'='*60}\n  STEP: {name}\n{'='*60}", flush=True)
        r = runner(argv)
        if r.returncode != 0:
            print(f"\nStep '{name}' FAILED (exit {r.returncode}). Fix it, then resume with: "
                  f"python scraper/trace_pipeline/finalize.py --from {name}")
            return name
    return None

def selftest():
    fails = []
    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    steps = build_steps(skip_backup=False, from_step=None)
    check("4 steps in order", [n for n, _ in steps] == ["backup", "precompute", "evidence", "embed"])
    check("backup argv", steps[0][1][1].endswith(os.path.join("backend", "backup_db.py")))
    check("evidence argv has --build-evidence", steps[2][1][-1] == "--build-evidence")
    check("embed argv has --embed", steps[3][1][-1] == "--embed")
    check("skip-backup drops backup", [n for n, _ in build_steps(True, None)] == ["precompute", "evidence", "embed"])
    check("--from evidence resumes there", [n for n, _ in build_steps(False, "evidence")] == ["evidence", "embed"])

    ran = []
    def ok_runner(argv):
        ran.append(argv[1])
        class R: returncode = 0
        return R()
    check("all steps run on success", run_steps(steps, ok_runner) is None and len(ran) == 4)
    def fail_second(argv):
        class R: returncode = 0 if len(ran2) == 0 else 1
        ran2.append(argv[1])
        return R()
    ran2 = []
    check("stops at first failure, names it", run_steps(steps, fail_second) == "precompute" and len(ran2) == 2)

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--skip-backup", action="store_true")
    p.add_argument("--from", dest="from_step", choices=STEP_ORDER)
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        sys.exit(selftest())
    failed = run_steps(build_steps(args.skip_backup, args.from_step),
                       lambda argv: subprocess.run(argv, cwd=_REPO_ROOT))
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main()
