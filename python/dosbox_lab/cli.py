# SPDX-License-Identifier: GPL-2.0-or-later
"""Command line: a thin shell around the library. Output is always JSON."""
import argparse
import json
import sys

from .api import Dosbox
from .batch import run_batch
from .errors import DmcpError
from .instance import RUNS, Mount, _pid_alive
from .scenario import play_scenario


def _instances():
    found = []
    for info_path in RUNS.glob("*/instance.json"):
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
            info["running"] = _pid_alive(info["pid"])
            info["_mtime"] = info_path.stat().st_mtime
        except (OSError, ValueError, KeyError):
            continue  # skip broken or half-written leftovers
        found.append(info)
    return sorted(found, key=lambda i: i["_mtime"], reverse=True)


def newest_instance():
    for info in _instances():
        if info["running"]:
            return info["id"]
    return None


def _num(text):
    return int(text, 16)


def _int_rules(values):
    rules = []
    for value in values or []:
        num, _, ah = value.partition(":")
        int(num, 16)  # ValueError -> bad_argument (main)
        rule = {"number": num}
        if ah:
            int(ah, 16)
            rule["ah"] = ah
        rules.append(rule)
    return rules or None


def build_parser():
    p = argparse.ArgumentParser(prog="dosbox_lab")
    # --id is allowed before or after the command (SUPPRESS: the subparser must not overwrite it with None)
    idopt = argparse.ArgumentParser(add_help=False)
    idopt.add_argument("--id", default=argparse.SUPPRESS,
                       help="instance id (default: the most recently started running instance)")
    p.add_argument("--id", default=None, help="instance id (default: the most recently started running instance)")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name):
        return sub.add_parser(name, parents=[idopt])

    s = add("start")
    s.add_argument("--dir", required=True, help="host directory for drive C:")
    s.add_argument("--cdrom", help="host directory for CD drive D:")
    s.add_argument("--cmd", action="append", default=[], help="autoexec line (repeatable)")
    s.add_argument("--headless", action="store_true", help="no emulator window")
    s = add("batch")
    s.add_argument("--dir", required=True, help="host directory for drive C: (writable)")
    s.add_argument("--cmd", required=True, help="DOS command, e.g. \"TCC HELLO.C\"")
    s.add_argument("--d", help="host directory for drive D:")
    s.add_argument("--cdrom-d", action="store_true", help="mount D: read-only as a CD drive")
    s.add_argument("--timeout", type=float, default=60.0)
    s.add_argument("--max-output", type=int, default=16 * 1024 * 1024)
    s = add("scenario")
    s.add_argument("--file", required=True, help="scenario file (dosbox-lab-scenario/1)")
    s.add_argument("--var", action="append", default=[], help="variable NAME=VALUE (repeatable)")
    s.add_argument("--window", action="store_true", help="show the emulator window instead of running headless")
    s.add_argument("--open", action="store_true", help="leave the emulator running at the end")
    s.add_argument("--output-dir", help="output directory instead of .runs/<id>/out")
    for name in ("stop", "list", "status", "resume", "regs", "text", "video", "events-stop", "cov-stop",
                 "flow-stop", "modules", "calls-stop", "calls-status"):
        add(name)
    s = add("pause"); s.add_argument("--until", choices=["pmode"])
    s = add("events-start"); s.add_argument("--type", action="append")
    s.add_argument("--int", action="append", help="NUMBER or NUMBER:AH in hex, e.g. 21:3D (repeatable)")
    s.add_argument("--with-return", action="store_true"); s.add_argument("--max-bytes", type=int)
    s.add_argument("--preview", type=int, help="number of data bytes to show in a file event")
    s = add("calls-start"); s.add_argument("--target", action="append", help="address or symbol (repeatable)")
    s.add_argument("--range", nargs=2, action="append", metavar=("FROM", "TO"), help="address range (repeatable)")
    s.add_argument("--module", action="append"); s.add_argument("--int", action="append", help="NUMBER or NUMBER:AH in hex")
    s.add_argument("--stack", type=int); s.add_argument("--max-per-target", type=int, default=1000)
    s.add_argument("--max-bytes", type=int); s.add_argument("--name", default="calls")
    s.add_argument("--states", type=int, default=0); s.add_argument("--max-states", type=int, default=1000)
    s = add("func-run"); s.add_argument("--state", required=True)
    s.add_argument("--write", action="append", help="ADDRESS=HEX (repeatable)")
    s.add_argument("--reg", action="append", help="NAME=VALUE in hex (repeatable)")
    s.add_argument("--stack"); s.add_argument("--irqs", action="store_true")
    s.add_argument("--max-instructions", type=int); s.add_argument("--name", default="func")
    s.add_argument("--timeout", type=float, default=60.0)
    s = add("events-read"); s.add_argument("--since", type=int, default=0); s.add_argument("--max", type=int, default=50)
    s = add("cov-start"); s.add_argument("--hits", action="store_true"); s.add_argument("--max-blocks", type=int)
    s = add("cov-save"); s.add_argument("name", nargs="?", default="coverage")
    s = add("flow-start"); s.add_argument("--returns", action="store_true"); s.add_argument("--ring", type=int, default=4096)
    s = add("flow-save"); s.add_argument("name", nargs="?", default="indirect")
    s = add("flow-recent"); s.add_argument("count", nargs="?", type=int, default=100)
    s = add("segments"); s.add_argument("module"); s.add_argument("segments", help="JSON list of segments")
    s = add("symbols"); s.add_argument("path"); s.add_argument("--base")
    s = add("symbol"); s.add_argument("name")
    s = add("snapshot"); s.add_argument("label"); s.add_argument("--ext", action="store_true")
    s.add_argument("--until", choices=["pmode"])
    s = add("step"); s.add_argument("count", type=int, nargs="?", default=1)
    s = add("translate"); s.add_argument("address")
    s = add("read"); s.add_argument("address"); s.add_argument("length", type=_num)
    s = add("write"); s.add_argument("address"); s.add_argument("hex")
    s = add("dump"); s.add_argument("address"); s.add_argument("length", type=_num); s.add_argument("name")
    s = add("search"); s.add_argument("address"); s.add_argument("length", type=_num)
    g = s.add_mutually_exclusive_group(required=True); g.add_argument("--hex"); g.add_argument("--text")
    s = add("key"); s.add_argument("key")
    s = add("type"); s.add_argument("text"); s.add_argument("--layout", default="de")
    s = add("move"); s.add_argument("x", type=float); s.add_argument("y", type=float)
    s = add("click"); s.add_argument("x", type=float); s.add_argument("y", type=float)
    s.add_argument("--button", default="left")
    s = add("shot"); s.add_argument("name", nargs="?", default="screen")
    s.add_argument("--rect", help="x,y,width,height")
    s = add("until"); s.add_argument("--address"); s.add_argument("--pmode", action="store_true")
    s.add_argument("--paging", action="store_true"); s.add_argument("--cs"); s.add_argument("--cpl", type=int)
    s.add_argument("--expression"); s.add_argument("--timeout", type=float, default=10.0)
    s = add("step-over"); s.add_argument("--timeout", type=float, default=10.0)
    s = add("frame"); s.add_argument("count", nargs="?", type=int, default=1)
    s = add("eval"); s.add_argument("expression")
    s = add("speed"); s.add_argument("mode", choices=["normal", "turbo", "cycles"]); s.add_argument("--cycles", type=int)
    s = add("break"); s.add_argument("target"); s.add_argument("--condition"); s.add_argument("--once", action="store_true")
    s = add("unbreak"); s.add_argument("target", nargs="?"); s.add_argument("--all", action="store_true")
    s = add("break-int"); s.add_argument("number"); s.add_argument("--ah"); s.add_argument("--al")
    add("break-list")
    s = add("dis"); s.add_argument("address", nargs="?"); s.add_argument("--count", type=int, default=10)
    s.add_argument("--bits", type=int, choices=[16, 32]); s.add_argument("--name")
    s = add("watch"); s.add_argument("address"); s.add_argument("--length", type=_num, default=1)
    s.add_argument("--kind", default="write", choices=["read", "write", "both"])
    s.add_argument("--action", default="halt", choices=["halt", "log"])
    s = add("unwatch"); s.add_argument("id", nargs="?", type=int); s.add_argument("--all", action="store_true")
    add("watch-list")
    s = add("watch-log"); s.add_argument("name", nargs="?", default="accesses"); s.add_argument("--clear", action="store_true")
    s = add("state-save"); s.add_argument("name", nargs="?", default="state")
    s = add("state-load"); s.add_argument("name", nargs="?", default="state"); s.add_argument("--path")
    s.add_argument("--skip-check", action="store_true")
    s = add("dos"); s.add_argument("kind", choices=["programs", "mcb", "files", "target"])
    s = add("call"); s.add_argument("method"); s.add_argument("params", nargs="?", default="{}")
    return p


def run(args):
    if args.command == "list":
        return {"instances": [{k: v for k, v in i.items() if not k.startswith("_")} for i in _instances()]}
    if args.command == "start":
        mounts = [Mount("C", args.dir)] + ([Mount("D", args.cdrom, cdrom=True)] if args.cdrom else [])
        box = Dosbox.start(mounts=mounts, autoexec=args.cmd, headless=args.headless)
        return {"id": box.id, "rundir": str(box.inst.rundir)}
    if args.command == "batch":
        mounts = [Mount("C", args.dir)] + ([Mount("D", args.d, cdrom=args.cdrom_d)] if args.d else [])
        return run_batch(mounts, args.cmd, timeout_s=args.timeout, max_output=args.max_output)
    if args.command == "scenario":
        variables = {}
        for item in args.var:
            if "=" not in item:
                raise DmcpError("bad_argument", f"--var needs the form NAME=VALUE: {item}")
            k, v = item.split("=", 1)
            variables[k] = v
        _box, result = play_scenario(args.file, variables=variables, headless=not args.window,
                                       stop_at_end=not args.open, output_dir=args.output_dir)
        return result
    inst_id = args.id or newest_instance()
    if inst_id is None:
        raise DmcpError("bad_argument", "no running instance (run start first)")
    box = Dosbox.attach(inst_id)
    if args.command == "stop":
        box.stop()
        return {"stopped": inst_id}
    if args.command == "status": return box.status()
    if args.command == "resume": return box.resume()
    if args.command == "step": return box.step(args.count)
    if args.command == "regs": return box.registers()
    if args.command == "translate": return box.translate(args.address)
    if args.command == "read": return box.mem_read_info(args.address, args.length)
    if args.command == "write": return box.mem_write(args.address, bytes.fromhex(args.hex))
    if args.command == "dump": return box.mem_dump(args.address, args.length, args.name)
    if args.command == "search":
        pattern = args.text if args.text is not None else bytes.fromhex(args.hex)
        return {"hits": box.mem_search(args.address, args.length, pattern)}
    if args.command == "key": return box.key(args.key)
    if args.command == "type": return box.type_text(args.text, layout=args.layout)
    if args.command == "move": return box.mouse_move(args.x, args.y)
    if args.command == "click": return box.mouse_click(args.x, args.y, button=args.button)
    if args.command == "shot":
        rect = tuple(int(v) for v in args.rect.split(",")) if args.rect else None
        return box.screenshot(args.name, rect=rect)
    if args.command == "text": return box.screen_text()
    if args.command == "video": return box.video_state()
    if args.command == "pause": return box.pause(until=args.until)
    if args.command == "events-start":
        return box.events_start(types=args.type, int_filter=_int_rules(args.int), with_return=args.with_return,
                                max_bytes=args.max_bytes, file_preview=args.preview)
    if args.command == "calls-start":
        ranges = [{"from": v, "to": b} for v, b in args.range] if args.range else None
        return box.calls_start(targets=args.target, ranges=ranges, modules=args.module,
                               ints=_int_rules(args.int) if args.int else None, stack_bytes=args.stack,
                               max_per_target=args.max_per_target, max_bytes=args.max_bytes, name=args.name,
                               states=args.states, max_states=args.max_states)
    if args.command == "func-run":
        def pairs(items, what):
            for item in items or []:
                if "=" not in item:
                    raise DmcpError("bad_argument", f"{what} needs the form A=B: {item}")
            return [item.split("=", 1) for item in items or []]
        write = [{"address": a, "hex": h} for a, h in pairs(args.write, "--write")]
        regs = dict(pairs(args.reg, "--reg"))
        return box.func_run(args.state, write=write or None, regs=regs or None, stack=args.stack,
                            irqs=args.irqs, max_instructions=args.max_instructions, name=args.name, timeout_s=args.timeout)
    if args.command == "calls-stop": return box.calls_stop()
    if args.command == "calls-status": return box.calls_status()
    if args.command == "events-stop": return box.events_stop()
    if args.command == "events-read": return box.events_read(args.since, args.max)
    if args.command == "cov-start": return box.coverage_start(args.max_blocks, hits=args.hits)
    if args.command == "cov-stop": return box.coverage_stop()
    if args.command == "cov-save": return box.coverage_save(args.name)
    if args.command == "flow-start": return box.flow_start(ring_size=args.ring, with_returns=args.returns)
    if args.command == "flow-stop": return box.flow_stop()
    if args.command == "flow-save": return box.flow_save(args.name)
    if args.command == "flow-recent": return box.flow_recent(args.count)
    if args.command == "modules": return {"modules": box.modules()}
    if args.command == "segments": return box.set_segments(args.module, json.loads(args.segments))
    if args.command == "symbols": return box.symbols_load(args.path, args.base)
    if args.command == "symbol": return box.symbol_lookup(name=args.name)
    if args.command == "snapshot": return box.snapshot(args.label, with_extension=args.ext, until=args.until)
    if args.command == "until":
        return box.run_until(address=args.address, pmode=args.pmode or None, paging=args.paging or None,
                             cs=args.cs, cpl=args.cpl, expression=args.expression, timeout_s=args.timeout)
    if args.command == "step-over": return box.step_over(timeout_s=args.timeout)
    if args.command == "frame": return box.frame_step(args.count)
    if args.command == "eval": return box.eval(args.expression)
    if args.command == "speed": return box.speed(args.mode, cycles=args.cycles)
    if args.command == "break":
        return box.break_add(args.target, condition=args.condition, once=args.once)
    if args.command == "unbreak":
        return box.break_remove(all=True) if args.all else box.break_remove(args.target)
    if args.command == "break-int": return box.break_int_add(args.number, ah=args.ah, al=args.al)
    if args.command == "break-list": return box.break_list()
    if args.command == "dis": return box.disassemble(args.address, count=args.count, bits=args.bits, name=args.name)
    if args.command == "watch":
        return box.watch_add(args.address, length=args.length, kind=args.kind, action=args.action)
    if args.command == "unwatch":
        return box.watch_remove(all=True) if args.all else box.watch_remove(args.id)
    if args.command == "watch-list": return box.watch_list()
    if args.command == "watch-log": return box.watch_log(args.name, clear=args.clear)
    if args.command == "state-save": return box.state_save(args.name)
    if args.command == "state-load":
        return box.state_load(args.name, path=args.path, skip_check=args.skip_check)
    if args.command == "dos":
        return {"programs": box.dos_programs, "mcb": box.dos_mcb, "files": box.dos_files,
                "target": box.dos_target}[args.kind]()
    if args.command == "call":
        params = json.loads(args.params)
        if not isinstance(params, dict):
            raise DmcpError("bad_argument", "params must be a JSON object, e.g. '{\"count\": 2}'")
        return box.call(args.method, **params)
    raise DmcpError("bad_argument", f"unknown command {args.command}")


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except DmcpError as exc:
        print(json.dumps({"error": exc.code, "message": exc.message}, ensure_ascii=True))
        return 1
    except OSError as exc:  # e.g. unreadable output file; start errors are already converted by Instance.start
        print(json.dumps({"error": "internal", "message": f"operating system error: {exc}"}, ensure_ascii=True))
        return 1
    except (ValueError, TypeError) as exc:  # argument conversion (JSON, hex, --rect); JSONDecodeError is a ValueError
        print(json.dumps({"error": "bad_argument", "message": str(exc)}, ensure_ascii=True))
        return 1
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 1 if args.command == "scenario" and result.get("status") == "error" else 0
