import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bgstatus as b  # noqa: E402


class ParseFooter(unittest.TestCase):
    def check(self, text, want):
        self.assertEqual(b.parse_footer(text), want, text)

    # happy paths
    def test_claude_two_shells(self):
        self.check("⏵⏵ bypass permissions on · 2 shells · ⧉ index · ← 5 agents · ↓ to manage", {"shell": 2})

    def test_claude_one_shell(self):
        self.check("⏵⏵ bypass permissions on · 1 shell · ← 5 agents · ↓ …", {"shell": 1})

    def test_claude_done_line(self):
        self.check("✻ Churned for 11s · done 3:20 PM · 2 shells still running", {"shell": 2})

    def test_opencode_shell(self):
        self.check("⚡ 322 tok/s · ttft 551ms  ↓ 1 shell · 340.6K (32%)", {"shell": 1})

    def test_codex_background_terminal(self):
        self.check("  3 background terminals running · /ps to view", {"terminal": 3})

    def test_agents_and_tasks(self):
        self.check("· 2 agents running · 4 background tasks", {"agent": 2, "task": 4})

    def test_local_agents(self):
        self.check("1 local agent", {"agent": 1})

    def test_max_across_lines(self):
        self.check("1 shell\nfoo\n3 shells", {"shell": 3})

    # sad paths
    def test_claude_switcher_hint_ignored(self):
        self.check("⏵⏵ bypass permissions on (shift+tab to cycle) · ← 5 agents", {})

    def test_codex_switcher_hint_ignored(self):
        self.check("  ← for agents · ? for shortcuts", {})

    def test_plain_words_ignored(self):
        self.check("the shell is ready; tasks are listed", {})

    def test_zero_dropped(self):
        self.check("0 shells", {})

    # edge cases
    def test_empty_and_none(self):
        self.check("", {})
        self.check(None, {})

    def test_only_footer_lines_count(self):
        text = "9 shells\n" + "\n".join(f"line {i}" for i in range(20))
        self.check(text, {})

    def test_blank_lines_not_counted_in_window(self):
        self.check("2 shells\n\n\n\n\n\n\n\n\n\nx", {"shell": 2})

    def test_large_count(self):
        self.check("99 shells", {"shell": 99})

    def test_word_boundary(self):
        self.check("v12 shellsort", {})


class Render(unittest.TestCase):
    def test_describe_order_and_plural(self):
        self.assertEqual(b.describe({"task": 1, "shell": 2, "agent": 1}), "2 shells · 1 agent · 1 task")
        self.assertEqual(b.describe({}), "")

    def test_slot_width_and_count(self):
        self.assertEqual(b.slot(2, 0), "2 ⠋")
        self.assertEqual(b.slot(9, 1), "9 ⠙")
        self.assertEqual(b.slot(10, 0), "+ ⠋")
        self.assertEqual(b.slot(0, 5), f"{b.BLANK} {b.BLANK}")
        for n in (0, 1, 9, 10, 500):
            self.assertEqual(len(b.slot(n, 3)), 3)

    def test_slot_wraps_frames(self):
        self.assertEqual(b.slot(1, len(b.FRAMES)), b.slot(1, 0))

    def test_owner_example_slot(self):
        self.assertEqual(b.slot(2, 2), "2 ⠹")

    def test_metadata_active(self):
        st = b.PaneState("claude", {"shell": 2})
        argv = b.metadata_args("w1:p1", st, 0)
        self.assertIn("bg=2 ⠋", argv)
        self.assertIn("claude · 2 shells", argv)
        self.assertEqual(argv[argv.index("--ttl-ms") + 1], str(b.TTL_MS))
        self.assertEqual(argv[argv.index("--agent") + 1], "claude")

    def test_metadata_idle(self):
        argv = b.metadata_args("w1:p1", b.PaneState("codex"), 0)
        self.assertIn(f"bg={b.BLANK} {b.BLANK}", argv)
        self.assertIn("--clear-display-agent", argv)
        self.assertEqual(argv[argv.index("--ttl-ms") + 1], str(b.TTL_MS))

    def test_clear_args(self):
        argv = b.clear_args("w1:p1", "claude")
        self.assertEqual(argv[argv.index("--clear-token") + 1], "bg")
        self.assertIn("--clear-display-agent", argv)


class FakeHerdr:
    def __init__(self):
        self.agent_list = []
        self.screens = {}
        self.reports = []
        self.reads = []
        self.fail_read = set()
        self.fail_report = False

    def agents(self):
        return self.agent_list

    def read(self, pane):
        self.reads.append(pane)
        if pane in self.fail_read:
            raise RuntimeError("pane not found")
        return self.screens.get(pane, "")

    def report(self, argv):
        if self.fail_report:
            raise RuntimeError("server error")
        self.reports.append(argv)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def rows(reports):
    return [a[a.index("--token") + 1] for a in reports]


def ag(pane, agent="claude", status="idle", tab="w1:t1", ws="w1"):
    return {"pane_id": pane, "agent": agent, "agent_status": status, "tab_id": tab, "workspace_id": ws}


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.h = FakeHerdr()
        self.clock = Clock()
        self.logs = []
        self.m = b.Monitor(self.h, clock=self.clock, log=self.logs.append)
        self.h.agent_list = [ag("w1:p1"), ag("w1:p2", "opencode", "done")]

    def test_first_tick_sends_every_row(self):
        self.m.tick(read=True)
        self.assertEqual(sorted(a[2] for a in self.h.reports), ["w1:p1", "w1:p2"])
        self.assertIn(f"bg={b.BLANK} {b.BLANK}", self.h.reports[1])

    def test_unchanged_idle_rows_throttled_then_refreshed(self):
        self.m.tick(read=True)
        self.h.reports.clear()
        self.clock.t += 1
        self.m.tick(read=False)
        self.assertEqual(self.h.reports, [])
        self.clock.t += b.REFRESH
        self.m.tick(read=False)
        self.assertEqual(len(self.h.reports), 2)

    def test_spinner_advances_every_tick(self):
        self.h.screens["w1:p1"] = "· 2 shells ·"
        self.m.tick(read=True)
        self.h.reports.clear()
        self.m.tick(read=False)
        self.m.tick(read=False)
        self.assertEqual([a[2] for a in self.h.reports], ["w1:p1", "w1:p1"])
        self.assertEqual(rows(self.h.reports), ["bg=2 ⠹", "bg=2 ⠸"])

    def test_reads_only_when_asked_or_new(self):
        self.m.tick(read=True)
        self.assertEqual(len(self.h.reads), 2)
        self.m.tick(read=False)
        self.assertEqual(len(self.h.reads), 2)
        self.h.agent_list.append(ag("w1:p3", "codex"))
        self.m.tick(read=False)
        self.assertEqual(self.h.reads[-1], "w1:p3")

    def test_work_ends_clears_display(self):
        self.h.screens["w1:p1"] = "1 shell"
        self.m.tick(read=True)
        self.h.screens["w1:p1"] = "done"
        self.h.reports.clear()
        self.m.tick(read=True)
        p1 = [a for a in self.h.reports if a[2] == "w1:p1"]
        self.assertEqual(len(p1), 1)
        self.assertIn("--clear-display-agent", p1[0])

    def test_pane_read_failure_clears_counts(self):
        self.h.screens["w1:p1"] = "2 shells"
        self.m.tick(read=True)
        self.h.fail_read.add("w1:p1")
        self.m.tick(read=True)
        self.assertEqual(self.m.panes["w1:p1"].counts, {})
        self.assertTrue(any("read w1:p1" in m for m in self.logs))

    def test_report_failure_retries_next_tick(self):
        self.h.fail_report = True
        self.m.tick(read=True)
        self.assertTrue(any("report" in m for m in self.logs))
        self.h.fail_report = False
        self.m.tick(read=False)
        self.assertEqual(len(self.h.reports), 2)

    def test_closed_pane_forgotten(self):
        self.m.tick(read=True)
        self.h.agent_list = self.h.agent_list[1:]
        self.m.tick(read=True)
        self.assertNotIn("w1:p1", self.m.panes)

    def test_agent_change_resets_state(self):
        self.h.screens["w1:p1"] = "2 shells"
        self.m.tick(read=True)
        self.h.agent_list[0] = ag("w1:p1", "codex")
        self.h.screens["w1:p1"] = ""
        self.m.tick(read=False)
        self.assertEqual(self.m.panes["w1:p1"].agent, "codex")
        self.assertEqual(self.m.panes["w1:p1"].counts, {})

    def test_only_filter(self):
        m = b.Monitor(self.h, only=["w1:p2"], clock=self.clock)
        m.tick(read=True)
        self.assertEqual([a[2] for a in self.h.reports], ["w1:p2"])

    def test_non_agent_entries_skipped(self):
        self.h.agent_list += [{"pane_id": "w1:p3"}, {"agent": "claude"}]
        self.m.tick(read=True)
        self.assertEqual(len(self.h.reports), 2)

    def test_empty_agent_list(self):
        self.h.agent_list = []
        self.m.tick(read=True)
        self.assertEqual(self.h.reports, [])

    def test_clear_all(self):
        self.m.tick(read=True)
        self.h.reports.clear()
        self.m.clear_all()
        self.assertEqual(len(self.h.reports), 2)
        self.assertTrue(all("--clear-token" in a for a in self.h.reports))


class Lifecycle(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["HERDR_PLUGIN_STATE_DIR"] = self.tmp.name
        os.environ["HERDR_SOCKET_PATH"] = "/nonexistent/test.sock"

    def tearDown(self):
        del os.environ["HERDR_PLUGIN_STATE_DIR"]
        del os.environ["HERDR_SOCKET_PATH"]
        self.tmp.cleanup()

    def quiet(self, argv):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return b.main(argv)

    def test_status_not_running(self):
        self.assertEqual(self.quiet(["status"]), 1)

    def test_stop_timeout_returns_error_and_restart_does_not_start(self):
        with mock.patch.object(b, "running_pid", return_value=999999), \
             mock.patch.object(b.os, "kill"), mock.patch.object(b, "STOP_TIMEOUT", 0.2), \
             mock.patch.object(b.subprocess, "Popen") as popen:
            self.assertEqual(self.quiet(["stop"]), 1)
            self.assertEqual(self.quiet(["restart"]), 1)
            popen.assert_not_called()

    def test_stop_when_process_already_gone(self):
        with mock.patch.object(b, "running_pid", return_value=999999), \
             mock.patch.object(b.os, "kill", side_effect=ProcessLookupError):
            self.assertEqual(self.quiet(["stop"]), 0)

    def test_stale_pidfile_is_not_trusted(self):
        with open(b.pidfile(), "w") as f:
            f.write(str(os.getpid()))  # a live PID, but nobody holds the lock
        self.assertIsNone(b.running_pid())

    def test_garbage_pidfile(self):
        with open(b.pidfile(), "w") as f:
            f.write("not-a-pid")
        self.assertIsNone(b.running_pid())

    def test_lock_is_exclusive(self):
        lock = b.acquire_lock()
        self.assertIsNotNone(lock)
        try:
            self.assertEqual(b.running_pid(), os.getpid())
            import subprocess
            code = ("import sys; sys.path.insert(0, %r); import bgstatus as b; "
                    "print(b.acquire_lock() is None)") % os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=os.environ)
            self.assertEqual(out.stdout.strip(), "True")
        finally:
            lock.close()
        self.assertIsNone(b.running_pid())

    def test_only_filter_uses_own_lock(self):
        lock = b.acquire_lock()
        try:
            other = b.acquire_lock("w1:p1", wait=0)
            self.assertIsNotNone(other)
            other.close()
        finally:
            lock.close()

    def test_lock_retry_gives_up(self):
        lock = b.acquire_lock()
        try:
            import subprocess, time as t
            code = ("import sys; sys.path.insert(0, %r); import bgstatus as b; "
                    "print(b.acquire_lock(wait=0.2) is None)") % os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            t0 = t.monotonic()
            out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=os.environ)
            self.assertEqual(out.stdout.strip(), "True")
            self.assertGreaterEqual(t.monotonic() - t0, 0.2)
        finally:
            lock.close()

    def test_signal_handlers_restored(self):
        import signal
        before = signal.getsignal(signal.SIGINT)
        self.quiet(["run", "--quiet"])
        self.assertIs(signal.getsignal(signal.SIGINT), before)

    def test_default_state_dir_matches_herdr(self):
        del os.environ["HERDR_PLUGIN_STATE_DIR"]
        try:
            self.assertTrue(b.state_dir().endswith(os.path.join("herdr", "plugins", "elderberry.bgstatus")))
        finally:
            os.environ["HERDR_PLUGIN_STATE_DIR"] = self.tmp.name

    def test_pidfile_per_server(self):
        a = b.pidfile()
        os.environ["HERDR_SOCKET_PATH"] = "/other/server.sock"
        self.assertNotEqual(a, b.pidfile())

    def test_stop_when_not_running(self):
        self.assertEqual(self.quiet(["stop"]), 0)

    def test_run_exits_when_socket_missing(self):
        # socket path does not exist -> loop exits at once and releases the lock
        self.assertEqual(self.quiet(["run", "--quiet"]), 0)
        self.assertIsNone(b.running_pid())

    def test_run_exits_if_lock_taken(self):
        lock = b.acquire_lock()
        try:
            self.assertEqual(self.quiet(["run", "--quiet"]), 0)
            self.assertEqual(b.running_pid(), os.getpid())  # holder keeps the lock
        finally:
            lock.close()


if __name__ == "__main__":
    unittest.main()


class ScanFailures(unittest.TestCase):
    def test_exits_after_max_failures(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": d}):
            os.environ.pop("HERDR_SOCKET_PATH", None)
            calls = []

            class Broken:
                def __init__(self, *a, **k):
                    pass

                def agents(self):
                    calls.append(1)
                    raise RuntimeError("server gone")

            with mock.patch.object(b, "Herdr", Broken), mock.patch.object(b.time, "sleep", lambda s: None):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(b.main(["run", "--quiet"]), 0)
            self.assertEqual(len(calls), b.MAX_SCAN_FAILS)
