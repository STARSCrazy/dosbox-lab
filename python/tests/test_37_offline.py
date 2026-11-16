"""Tests without an emulator: they run on every platform and in CI without a build."""
import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dosbox_lab import DmcpError, cli
from dosbox_lab import instance as instance_mod
from dosbox_lab.instance import Mount, _agent_text, _conf_text, _endpoint
from dosbox_lab.pipe import PipeClient

REPO = Path(__file__).resolve().parents[2]


class FakeFile:
    """Stands in for the pipe/socket: replies are fed in chunks, written requests are recorded."""

    def __init__(self, chunks=(), reply_ids=True):
        self.chunks = list(chunks)
        self.written = []
        self.closed = False
        self.reply_ids = reply_ids

    def write(self, data):
        self.written.append(json.loads(data.decode("utf-8")))

    def read(self, size):
        return self.chunks.pop(0) if self.chunks else b""

    def close(self):
        self.closed = True


def client_with(fake):
    client = PipeClient("/nonexistent")
    client._file = fake
    return client


class PipeClientTest(unittest.TestCase):
    def test_reply_in_several_chunks(self):
        fake = FakeFile([b'{"jsonrpc":"2.0","id":1,', b'"result":{"pong":true}}', b"\n"])
        self.assertEqual(client_with(fake).call("dmcp.ping"), {"pong": True})
        self.assertEqual(fake.written[0]["method"], "dmcp.ping")

    def test_error_code_from_data(self):
        fake = FakeFile([b'{"id":1,"error":{"message":"no","data":{"code":"bad_argument"}}}\n'])
        with self.assertRaises(DmcpError) as ctx:
            client_with(fake).call("emu.x")
        self.assertEqual((ctx.exception.code, ctx.exception.message), ("bad_argument", "no"))

    def test_eof_is_instance_dead(self):
        with self.assertRaises(DmcpError) as ctx:
            client_with(FakeFile([])).call("dmcp.ping")
        self.assertEqual(ctx.exception.code, "instance_dead")

    def test_reply_for_other_request_is_rejected(self):
        fake = FakeFile([b'{"id":7,"result":{}}\n'])
        client = client_with(fake)
        with self.assertRaises(DmcpError) as ctx:
            client.call("dmcp.ping")
        self.assertEqual(ctx.exception.code, "internal")
        self.assertTrue(fake.closed)

    def test_garbage_reply(self):
        with self.assertRaises(DmcpError) as ctx:
            client_with(FakeFile([b"\xff\xfe not json\n"])).call("dmcp.ping")
        self.assertEqual(ctx.exception.code, "internal")

    def test_interrupted_call_drops_connection(self):
        class Interrupting(FakeFile):
            def read(self, size):
                raise KeyboardInterrupt

        fake = Interrupting()
        client = client_with(fake)
        with self.assertRaises(KeyboardInterrupt):
            client.call("dmcp.ping")
        self.assertTrue(fake.closed)  # a late reply can never be taken for the next call's result
        self.assertIsNone(client._file)

    def test_two_replies_in_one_chunk(self):
        fake = FakeFile([b'{"id":1,"result":{"a":1}}\n{"id":2,"result":{"b":2}}\n'])
        client = client_with(fake)
        self.assertEqual(client.call("x"), {"a": 1})
        self.assertEqual(client.call("y"), {"b": 2})


class ConfigTextTest(unittest.TestCase):
    def test_mounts_autoexec_and_overrides(self):
        text = _conf_text([Mount("C", "/a b/c"), Mount("D", "/cd", cdrom=True)], ["GAME.EXE"],
                          {"cpu": {"cycles": "max"}, "new": {"k": "v"}})
        self.assertIn('mount C "/a b/c"\n', text)
        self.assertIn('mount D "/cd" -t cdrom\n', text)
        self.assertTrue(text.endswith("C:\nGAME.EXE\n"))
        self.assertIn("cycles=max", text)
        self.assertIn("core=normal", text)  # the instruction hook needs the normal core
        self.assertIn("[new]\nk=v", text)

    def test_agent_text_transport(self):
        text = _agent_text("/x/agent.sock", "/x/dosbox-x", "/x")
        expected = "named_pipe" if os.name == "nt" else "unix_socket"
        self.assertIn(f"transport={expected}\n", text)
        self.assertIn("endpoint=/x/agent.sock\n", text)

    @unittest.skipIf(os.name == "nt", "Unix socket path")
    def test_long_run_folder_uses_short_socket_path(self):
        short = _endpoint("abc", Path("/tmp/r"))
        self.assertEqual(short, "/tmp/r/agent.sock")
        long_dir = Path("/tmp") / ("x" * 120)
        path = _endpoint("abc", long_dir)
        self.addCleanup(shutil.rmtree, Path(path).parent, True)
        self.assertLess(len(os.fsencode(path)), 100)
        self.assertEqual(Path(path).parent.stat().st_mode & 0o777, 0o700)


class CliTest(unittest.TestCase):
    def test_lengths_are_hex(self):
        args = cli.build_parser().parse_args(["read", "l:0", "10"])
        self.assertEqual(args.length, 16)

    def test_clean_removes_only_ended_instances(self):
        runs = Path(tempfile.mkdtemp(prefix="dmcp-runs-"))
        self.addCleanup(shutil.rmtree, runs, True)
        for inst_id, pid in (("dead", 0x7FFFFFF0), ("alive", os.getpid())):
            (runs / inst_id).mkdir()
            (runs / inst_id / "instance.json").write_text(json.dumps({"id": inst_id, "pid": pid}))
        alive = lambda pid: pid == os.getpid()  # noqa: E731
        with mock.patch.object(cli, "RUNS", runs), mock.patch.object(cli, "_pid_alive", alive):
            self.assertEqual(cli.clean_runs(), {"removed": ["dead"]})
        self.assertEqual(sorted(p.name for p in runs.iterdir()), ["alive"])

    def test_stop_unknown_instance(self):
        with mock.patch("builtins.print"):
            self.assertEqual(cli.main(["stop", "--id", "does-not-exist"]), 1)


class ExeLookupTest(unittest.TestCase):
    def test_default_exe_per_platform(self):
        if os.environ.get("DOSBOX_LAB_EXE") or os.environ.get("DOSBOX_MCP_EXE"):
            self.skipTest("DOSBOX_LAB_EXE overrides the default")
        name = "dosbox-lab.exe" if os.name == "nt" else "dosbox-x"
        self.assertEqual(instance_mod.DEFAULT_EXE.name, name)


class ToolDocsTest(unittest.TestCase):
    def test_every_mcp_tool_is_documented(self):
        try:
            from dosbox_lab import mcp_server
        except ImportError:
            self.skipTest("mcp package not installed")
        tools = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
        doc = (REPO / "docs" / "dosbox-lab" / "tools.md").read_text(encoding="utf-8")
        documented = set()
        for line in doc.splitlines():  # "- `a` / `b` - description"
            if line.startswith("- `"):
                documented.update(re.findall(r"`([a-z_0-9]+)`", line.split(" - ", 1)[0]))
        self.assertEqual(tools - documented, set(), "tools missing in docs/dosbox-lab/tools.md")
        self.assertEqual(documented - tools, set(), "documented tools that do not exist")

    def test_tools_run_off_the_event_loop(self):
        try:
            from dosbox_lab import mcp_server
        except ImportError:
            self.skipTest("mcp package not installed")
        tool = mcp_server.mcp._tool_manager.get_tool("dosbox_list")
        self.assertTrue(tool.is_async)


class TestprogsTest(unittest.TestCase):
    """The checked-in DOS binaries are what NASM builds from the sources (NASM output is deterministic)."""

    def test_binaries_match_sources(self):
        nasm = os.environ.get("NASM") or shutil.which("nasm")
        if not nasm:
            self.skipTest("NASM not installed")
        progs = REPO / "testprogs"
        out = Path(tempfile.mkdtemp(prefix="dmcp-nasm-"))
        self.addCleanup(shutil.rmtree, out, True)
        sources = sorted(progs.glob("*.asm"))
        self.assertTrue(sources)
        for src in sources:
            exe = any(line.startswith("; OUTPUT=EXE") for line in src.read_text(encoding="latin-1").splitlines())
            name = src.stem.upper() + (".EXE" if exe else ".COM")
            built = out / name
            subprocess.run([nasm, "-f", "bin", "-i", str(progs) + os.sep, str(src), "-o", str(built)],
                           check=True, capture_output=True)
            with self.subTest(program=name):
                self.assertEqual(built.read_bytes(), (progs / name).read_bytes(),
                                 f"{name} is out of date: rebuild testprogs")


if __name__ == "__main__":
    unittest.main()
