#!/usr/bin/env python
"""Outcome gate for the test suite.

Runs pytest, builds a map ``test id -> outcome`` and compares it with the baseline saved in
``tests/gates/outcome_baseline.json``.

    python tests/gates/run_gate.py                                  # full run, compare with the baseline
    python tests/gates/run_gate.py --save-baseline                  # full run, write the baseline
    python tests/gates/run_gate.py --save-on-pass                   # full run, compare, write the baseline if passed
    python tests/gates/run_gate.py --targeted "tests/test_x*.py"    # run the files that match, compare those ids
    python tests/gates/run_gate.py --workers 0                      # 1 serial process (default: 8 xdist workers)

Outcomes: passed, failed, error, skipped, xfailed, collect_error. A collection error has the id
``<file>::<collection>``. The test ids have the form ``file::Class::test`` (as in check_gate.py).

Full compare fails (exit 1) if:
  - a baseline ``passed`` test is missing or does not pass, and it is not in removed_tests.txt;
  - a test that is not in the baseline does not pass;
  - a test is failed, error or collect_error, and it did not have the same outcome in the baseline;
  - a test is skipped or xfailed, it was not so in the baseline, and it is not in allowed_skips.txt.
A full run prints a WARNING (no change of the exit code) when the number of test ids or test files is over
tests/gates/test_budget.json.
Targeted compare uses only the collected ids (a baseline id that was not collected is not treated as removed).

removed_tests.txt and allowed_skips.txt: one line per test, ``<test id>  # <reason>``. Blank lines and lines that
start with ``#`` are ignored. An allowed_skips.txt line can also name a file or a class: it covers all its tests.

Exit codes: 0 gate passed, 1 gate failed or the run is not trustworthy.
"""

import argparse
import glob
import json
import os
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATES = Path(__file__).resolve().parent
BASELINE = GATES / 'outcome_baseline.json'
REMOVED = GATES / 'removed_tests.txt'
ALLOWED_SKIPS = GATES / 'allowed_skips.txt'
BUDGET = GATES / 'test_budget.json'

OUTCOMES = ['passed', 'failed', 'error', 'skipped', 'xfailed', 'collect_error']
# when a test has more than 1 report (setup, call, teardown) the worst outcome wins
SEVERITY = {'passed': 1, 'skipped': 2, 'xfailed': 2, 'error': 4, 'failed': 5, 'collect_error': 6}


def _norm(node_id):
    return node_id.replace('\\', '/')


class OutcomePlugin(object):
    """Records the outcome of each test with its pytest node id."""

    def __init__(self):
        self.outcomes = {}
        self.subtest_count = 0

    def _put(self, node_id, outcome):
        node_id = _norm(node_id)
        old = self.outcomes.get(node_id)
        if old is None or SEVERITY[outcome] > SEVERITY[old]:
            self.outcomes[node_id] = outcome

    def pytest_collectreport(self, report):
        if report.failed:
            self._put('%s::<collection>' % report.nodeid, 'collect_error')

    def pytest_runtest_logreport(self, report):
        # pytest 9 counts subtest reports separately, but folds them into their parent's XML case.
        if report.when == 'call' and hasattr(report, 'context'):
            self.subtest_count += 1
        if report.when == 'call':
            if report.failed:
                outcome = 'failed'
            elif report.skipped:
                outcome = 'xfailed' if hasattr(report, 'wasxfail') else 'skipped'
            else:
                outcome = 'passed'
        else:
            if report.failed:
                outcome = 'error'
            elif report.skipped:
                outcome = 'xfailed' if hasattr(report, 'wasxfail') else 'skipped'
            elif report.when == 'setup':
                outcome = 'passed'
            else:
                return
        self._put(report.nodeid, outcome)


def read_id_list(path):
    """Ids of a text file: ``<test id>  # <reason>`` per line; blank and comment lines are skipped."""
    ids = set()
    if not path.exists():
        return ids
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        ids.add(_norm(line.split('  #', 1)[0].strip()))
    return ids


def check_junit(junit_path, subtest_count=0):
    """Check the JUnit report. Return (error text or None, dict outcome -> count from the report).

    :param subtest_count: the number of additional subtest reports recorded by the pytest plugin.
        They count in the suite total but share their parent's XML case.
    """
    if not os.path.exists(junit_path):
        return 'the JUnit report does not exist', {}
    try:
        root = ET.parse(junit_path).getroot()
    except ET.ParseError as err:
        return 'the JUnit report does not parse: %s' % err, {}
    suites = [root] if root.tag == 'testsuite' else list(root.iter('testsuite'))
    if not suites:
        return 'the JUnit report has no testsuite', {}
    declared = sum(int(s.get('tests', '0')) for s in suites)
    cases = list(root.iter('testcase'))
    if len(cases) < 1:
        return 'the JUnit report has no test case', {}
    # A shorter report is valid only when the plugin observed the extra subtest reports.
    if len(cases) > declared:
        return 'the JUnit report has %d test cases but the testsuite says %d' % (len(cases), declared), {}
    if len(cases) + subtest_count != declared:
        return ('the JUnit report declares %d tests, has %d test case(s), '
                'and the plugin recorded %d additional subtest(s)' %
                (declared, len(cases), subtest_count)), {}
    counts = {}
    for case in cases:
        if case.find('failure') is not None:
            kind = 'failed'
        elif case.find('error') is not None:
            kind = 'error'
        elif case.find('skipped') is not None:
            kind = 'xfailed' if case.find('skipped').get('type') == 'pytest.xfail' else 'skipped'
        else:
            kind = 'passed'
        counts[kind] = counts.get(kind, 0) + 1
    return None, counts


def default_workers():
    """8 pytest-xdist workers if the package is installed, else 0 (1 serial process)."""
    try:
        # optional package: its absence is the normal case, so the import stays in the try
        import xdist  # noqa: F401
    except ImportError:
        return 0
    return min(8, os.cpu_count() or 1)


def run_pytest(targets, workers=0):
    """Run pytest in this process. Return (return code, outcome map, junit error, junit counts).

    :param workers: the number of pytest-xdist workers; 0 runs all tests in this process. The workers get whole
        files (--dist loadfile), so the tests of 1 file keep their order and their module state.
    """
    import pytest
    os.chdir(str(ROOT))
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    fd, junit_path = tempfile.mkstemp(suffix='.xml', prefix='run_gate_')
    os.close(fd)
    os.remove(junit_path)
    plugin = OutcomePlugin()
    parallel = ['-n', str(workers), '--dist', 'loadfile'] if workers > 0 else []
    try:
        code = pytest.main(targets + parallel + ['-q', '-p', 'no:cacheprovider', '--continue-on-collection-errors',
                                                 '--junitxml', junit_path], plugins=[plugin])
        junit_error, junit_counts = check_junit(junit_path, subtest_count=plugin.subtest_count)
    finally:
        if os.path.exists(junit_path):
            os.remove(junit_path)
    return int(code), plugin.outcomes, junit_error, junit_counts


def expand_patterns(patterns):
    files = []
    for pattern in patterns:
        found = sorted(glob.glob(pattern, root_dir=str(ROOT), recursive=True))
        found = [f for f in found if os.path.isfile(os.path.join(str(ROOT), f)) or os.path.isabs(f)]
        if not found:
            print('GATE FAILED: the pattern matches no file: %s' % pattern)
            return None
        for f in found:
            if f not in files:
                files.append(f)
    return files


def _allowed(node_id, allowed):
    """True if allowed_skips.txt names the test, or its file or class (an opt-in group such as DipTrace)."""
    return node_id in allowed or any(node_id.startswith(entry + '::') for entry in allowed)


def compare(current, baseline, removed, allowed, full):
    """Return the list of failure lines. Print each change of outcome."""
    failures = []
    for node_id in sorted(current):
        now = current[node_id]
        was = baseline.get(node_id)
        if was != now:
            print('  change  %s: %s -> %s' % (node_id, was or 'new', now))
        if was is None:
            # a new test may skip only if allowed_skips.txt names it (a platform or opt-in skip)
            if now != 'passed' and not (now in ('skipped', 'xfailed') and _allowed(node_id, allowed)):
                failures.append('new test does not pass (%s): %s' % (now, node_id))
        elif now in ('failed', 'error', 'collect_error'):
            if now != was:
                failures.append('%s (was %s): %s' % (now, was, node_id))
        elif now in ('skipped', 'xfailed'):
            if now != was and not _allowed(node_id, allowed):
                failures.append('%s (was %s) and not in allowed_skips.txt: %s' % (now, was, node_id))
        elif now == 'passed' and was == 'passed':
            pass
    if full:
        for node_id in sorted(baseline):
            if baseline[node_id] == 'passed' and node_id not in current and node_id not in removed:
                print('  change  %s: passed -> missing' % node_id)
                failures.append('baseline passed test is missing and not in removed_tests.txt: %s' % node_id)
    return failures


def budget_warning(node_ids):
    """Return a warning text when the number of test ids or test files is over tests/gates/test_budget.json.

    It is a warning only: it never changes the exit code.
    """
    if not BUDGET.exists():
        return None
    budget = json.loads(BUDGET.read_text(encoding='utf-8'))
    files = {i.split('::', 1)[0] for i in node_ids}
    lines = []
    for what, count, limit in (('tests', len(node_ids), budget.get('max_tests')),
                               ('files', len(files), budget.get('max_files'))):
        if limit is not None and count > limit:
            lines.append('test budget exceeded: %d %s > %d' % (count, what, limit))
    if not lines:
        return None
    lines.append('fold tests into topical files; the owner decides a raise of tests/gates/test_budget.json')
    return '\n'.join(['*' * 78] + ['WARNING  ' + line for line in lines] + ['*' * 78])


LAST_OUTCOMES = {}   # the outcome map of the last main() run (run_core.py reads it)


def save_baseline(current):
    BASELINE.write_text(json.dumps({k: current[k] for k in sorted(current)}, indent=1) + '\n',
                        encoding='utf-8', newline='\n')
    print('baseline saved: %s' % BASELINE.relative_to(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--save-baseline', action='store_true', help='full run, write outcome_baseline.json')
    parser.add_argument('--save-on-pass', action='store_true',
                        help='full run, compare, and write outcome_baseline.json only if the gate passes')
    parser.add_argument('--targeted', nargs='+', metavar='PATTERN', help='run only the files that match')
    parser.add_argument('--workers', type=int, default=default_workers(),
                        help='pytest-xdist workers (default: 8 if pytest-xdist is installed); 0 = 1 serial process')
    args = parser.parse_args()

    if (args.save_baseline or args.save_on_pass) and args.targeted:
        print('GATE FAILED: --save-baseline and --save-on-pass work in full mode only')
        return 1

    if args.targeted:
        targets = expand_patterns(args.targeted)
        if targets is None:
            return 1
    else:
        targets = ['tests']

    start = time.time()
    code, current, junit_error, junit_counts = run_pytest(targets, args.workers)
    elapsed = time.time() - start

    if code not in (0, 1):
        print('GATE FAILED: pytest returned exit code %d (2 interrupted, 3 internal error, 4 usage error, '
              '5 no tests collected)' % code)
        return 1
    if junit_error:
        print('GATE FAILED: %s' % junit_error)
        return 1
    if not current:
        print('GATE FAILED: no test outcome was recorded')
        return 1

    counts = {o: 0 for o in OUTCOMES}
    for outcome in current.values():
        counts[outcome] += 1
    print('\nrun_gate: %d ids in %.0f s: %s' % (len(current), elapsed,
                                                ', '.join('%s %d' % (o, counts[o]) for o in OUTCOMES)))

    LAST_OUTCOMES.update(current)
    if not args.targeted:
        warning = budget_warning(list(current))
        if warning:
            print(warning)

    if args.save_baseline:
        save_baseline(current)
        return 0

    if not BASELINE.exists():
        print('GATE FAILED: %s is missing (run with --save-baseline)' % BASELINE.name)
        return 1
    baseline = json.loads(BASELINE.read_text(encoding='utf-8'))
    failures = compare(current, baseline, read_id_list(REMOVED), read_id_list(ALLOWED_SKIPS), not args.targeted)
    for line in failures:
        print('  FAIL    %s' % line)
    print('GATE FAILED: %d problem(s)' % len(failures) if failures else 'GATE PASSED')
    if failures:
        return 1
    if args.save_on_pass:
        save_baseline(current)
    return 0


if __name__ == '__main__':
    sys.exit(main())
