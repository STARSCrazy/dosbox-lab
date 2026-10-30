#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "agent/dmcp.h"
#include "callback.h"
#include "cpu.h"
#include "regs.h"
#include "pic.h"
#include "video.h"
#include "dmcp_addr.h"
#include "dmcp_disasm.h"
#include "dmcp_expr.h"
#include "dmcp_internal.h"
#include "dmcp_modules.h"
#include "dmcp_record.h"
#include "dmcp_watch.h"

bool CPU_IsHLTed(void); // cpu.cpp: guest executed HLT and waits for an interrupt

#include <chrono>
#include <cstdio>
#include <map>
#include <set>
#include <thread>
#include <vector>

extern bool ticksLocked;

bool DMCP_hook_active = false;

namespace dmcp {
namespace {

bool g_paused = false;
std::string g_stop_reason = "none";
uint32_t g_stop_linear = 0;
struct Breakpoint {
    std::string quelle;   // address text as given (or "l:<hex>" for the legacy linear parameter)
    Address address;      // resolved when added
    Expr bedingung;
    bool einmalig = false;
    uint64_t treffer = 0; // stops (condition true)
};
std::map<uint32_t, Breakpoint> g_breakpoints; // key: linear address, one breakpoint per address

struct IntBreakpoint {
    uint8_t nummer = 0;
    int ah = -1; // -1: any
    int al = -1;
    uint64_t treffer = 0;
};
std::vector<IntBreakpoint> g_int_breakpoints;
uint8_t g_stop_int = 0;
uint16_t g_stop_ax = 0;
// Skip flag for the instruction we resume at: no checks there (breakpoints, stop condition). emu.continue sets it
// only after a stop decided at this very instruction (it must not hit twice; after a watch/pause/frame/step stop
// the instruction is checked once). run_until/step_over/frame_step from a paused state always set it (E4: at
// least one instruction).
bool g_skip_break_once = false;
uint32_t g_skip_linear = 0; // the instruction the skip flag applies to (an IRQ handler may run first)
// True while StepOne runs. A callback (DOS/BIOS service) may re-enter DOSBOX_RunMachine via
// CALLBACK_RunRealInt; that nested run must finish as part of the atomic step instead of pausing.
bool g_in_step = false;

// One stop condition shared by emu.pause bis=pmode, emu.run_until, emu.step_over and emu.frame_step.
// All set parts must hold at the same time.
struct Until {
    bool active = false;
    std::string reason;          // becomes stop_reason and wartet_auf
    bool pmode = false;          // protected mode, not v86
    bool paging = false;
    bool has_cs = false;
    uint16_t cs = 0;
    bool has_cpl = false;
    uint8_t cpl = 0;
    bool has_linear = false;
    uint32_t linear = 0;
    bool has_stack = false;      // step_over: (e)sp at or above the start value (recursion must not stop early)
    uint32_t stack_min = 0;
    uint64_t frames_left = 0;    // frame_step: stop after this many finished frames (checked in ExecOnFrame)
    Expr expr;
};
Until g_until;
uint64_t g_frames = 0; // finished frames since start (D35)

bool UntilMet(uint32_t here)
{
    const Until& u = g_until;
    if (u.frames_left != 0)
        return false; // frame_step stops from ExecOnFrame only
    if (u.pmode && !(cpu.pmode && !GETFLAG(VM)))
        return false;
    if (u.paging && (cpu.cr0 & 0x80000000u) == 0)
        return false;
    if (u.has_cs && SegValue(cs) != u.cs)
        return false;
    if (u.has_cpl && cpu.cpl != u.cpl)
        return false;
    if (u.has_linear && here != u.linear)
        return false;
    if (u.has_stack && (cpu.stack.big ? reg_esp : static_cast<uint32_t>(reg_sp)) < u.stack_min)
        return false;
    if (!u.expr.Empty() && u.expr.Eval() == 0)
        return false;
    return true;
}

JsonValue StatusObject()
{
    JsonValue result = JsonValue::Object();
    Set(&result, "paused", Bool(g_paused));
    Set(&result, "stop_reason", Str(g_stop_reason));
    if (g_stop_reason == "breakpoint")
        Set(&result, "stop_linear", Hex(g_stop_linear, 8));
    if (g_stop_reason == "int_breakpoint") {
        JsonValue info = JsonValue::Object();
        Set(&info, "nummer", Hex(g_stop_int, 2));
        Set(&info, "ax", Hex(g_stop_ax, 4));
        Set(&result, "stop_int", info);
    }
    if (g_stop_reason == "watch")
        Set(&result, "stop_watch", WatchStopJson());
    Set(&result, "turbo", Bool(ticksLocked));
    Set(&result, "cycles", Num(static_cast<uint64_t>(CPU_CycleMax)));
    Set(&result, "cycles_auto", Bool(CPU_CycleAutoAdjust));
    Set(&result, "halted", Bool(CPU_IsHLTed()));
    Set(&result, "pmode", Bool(cpu.pmode));
    Set(&result, "befehls_hook", Bool(DMCP_hook_active));
    Set(&result, "func", Bool(FuncActive()));
    Set(&result, "cs", Hex(SegValue(cs), 4));
    Set(&result, "eip", Hex(reg_eip, 8));
    if (g_until.active)
        Set(&result, "wartet_auf", Str(g_until.reason));
    Set(&result, "zeit_ms", NumFixed(PIC_FullIndex()));
    Set(&result, "bilder", Num(g_frames));
    Set(&result, "ram_bytes", Num(GuestRamBytes()));
    return result;
}

JsonValue BreakpointList()
{
    JsonValue result = JsonValue::Object();
    JsonValue list = JsonValue::Array();
    JsonValue entries = JsonValue::Array();
    for (const auto& item : g_breakpoints) {
        list.array.push_back(Hex(item.first, 8));
        const Breakpoint& bp = item.second;
        JsonValue entry = JsonValue::Object();
        JsonValue adr = AddressJson(bp.address);
        AnnotateAddress(bp.address, &adr, false);
        Set(&entry, "linear", Hex(item.first, 8));
        Set(&entry, "adresse", adr);
        Set(&entry, "quelle", Str(bp.quelle));
        if (!bp.bedingung.Empty())
            Set(&entry, "bedingung", Str(bp.bedingung.Text()));
        Set(&entry, "einmalig", Bool(bp.einmalig));
        Set(&entry, "treffer", Num(bp.treffer));
        entries.array.push_back(entry);
    }
    JsonValue ints = JsonValue::Array();
    for (const IntBreakpoint& ib : g_int_breakpoints) {
        JsonValue entry = JsonValue::Object();
        Set(&entry, "nummer", Hex(ib.nummer, 2));
        if (ib.ah >= 0)
            Set(&entry, "ah", Hex(static_cast<uint64_t>(ib.ah), 2));
        if (ib.al >= 0)
            Set(&entry, "al", Hex(static_cast<uint64_t>(ib.al), 2));
        Set(&entry, "treffer", Num(ib.treffer));
        ints.array.push_back(entry);
    }
    Set(&result, "breakpoints", list);
    Set(&result, "eintraege", entries);
    Set(&result, "int_haltepunkte", ints);
    return result;
}

// Breakpoint address from "adresse" (any form) or the legacy "linear" number.
bool BreakAddress(const JsonValue& params, Address* address, std::string* quelle, Error* error)
{
    std::string text;
    uint64_t linear = 0;
    if (ParamStr(params, "adresse", &text)) {
        if (!ParseAddress(text, address, error))
            return false;
        if (!address->has_linear)
            return Fail(error, "bad_argument", "breakpoint address needs a linear form (not p:)");
        *quelle = text;
        return true;
    }
    if (ParamU64(params, "linear", &linear) && linear <= 0xFFFFFFFFull) {
        char buffer[16];
        std::snprintf(buffer, sizeof(buffer), "l:%08X", static_cast<unsigned>(linear));
        *quelle = buffer;
        return ParseAddress(buffer, address, error);
    }
    return Fail(error, "bad_argument", "pass adresse (string) or linear (32-bit number)");
}

bool OptionalByte(const JsonValue& params, const char* name, int* out, Error* error)
{
    *out = -1;
    if (Param(params, name) == nullptr)
        return true;
    uint32_t value = 0;
    if (!HexField(params, name, &value) || value > 0xFF)
        return Fail(error, "bad_argument", std::string(name) + " must be a byte (hex string)");
    *out = static_cast<int>(value);
    return true;
}

// Vector of a software INT instruction at linear (CD xx, CC = 3, CE = 4 only if OF is set).
bool IntAt(uint32_t linear, uint8_t* vector)
{
    if ((!cpu.pmode || GETFLAG(VM)) && SegValue(cs) == CB_SEG)
        return false; // internal INT stubs of the emulator's own BIOS/DOS (as the int log, stage-2 E6)
    uint8_t b = 0;
    uint32_t i = 0;
    for (; i < 15; ++i) {
        if (!PeekLinear(linear + i, &b))
            return false;
        if (!(b == 0x66 || b == 0x67 || b == 0xF0 || b == 0xF2 || b == 0xF3 || b == 0x26 || b == 0x2E ||
              b == 0x36 || b == 0x3E || b == 0x64 || b == 0x65))
            break;
    }
    if (b == 0xCD)
        return PeekLinear(linear + i + 1, vector);
    if (b == 0xCC) {
        *vector = 3;
        return true;
    }
    if (b == 0xCE && GETFLAG(OF)) {
        *vector = 4;
        return true;
    }
    return false;
}

// Executes exactly one instruction like DEBUG_Run(1,...) does.
bool StepOne()
{
    g_skip_break_once = DMCP_hook_active; // the hook only consumes the flag while it runs
    g_skip_linear = static_cast<uint32_t>(SegPhys(cs) + reg_eip);
    g_in_step = true;
    CPU_CycleLeft += CPU_Cycles - 1;
    CPU_Cycles = 1;
    const Bits ret = (*cpudecoder)();
    if (ret > 0 && static_cast<Bitu>(ret) < CB_MAX)
        (*CallBack_Handlers[ret])();
    g_in_step = false;
    g_skip_break_once = false; // a step is atomic: never leave the flag behind (HLT executes nothing)
    return ret >= 0;
}

// Arms the shared stop condition, replacing an older one.
void Arm(const Until& until)
{
    g_until = until;
    g_until.active = true;
    UpdateHookActive();
}

void Disarm()
{
    g_until = Until();
    UpdateHookActive();
}

// Stops the hook decides before executing the instruction it stops at; resuming there must not hit again.
bool StopDecidedHere(const std::string& reason)
{
    return reason == "breakpoint" || reason == "int_breakpoint" || reason == "run_until" || reason == "step_over" ||
           reason == "pmode";
}

// Resume after arming a stop condition: nothing is checked at the current instruction (E4).
void ResumeArmed()
{
    const bool was_paused = g_paused;
    Resume();
    if (was_paused)
        g_skip_break_once = true;
}

// emu.speed: values before the first "cycles" call, restored by "normal"/"turbo".
bool g_speed_saved = false;
bool g_saved_cycles_auto = false;
cpu_cycles_count_t g_saved_cycle_max = 0;
cpu_cycles_count_t g_saved_old_cycle_max = 0;

} // namespace

void StopAt(const std::string& reason)
{
    g_paused = true;
    g_stop_reason = reason;
    g_skip_break_once = false; // a stop never leaves the skip flag behind (stage-2 parked point)
    FuncOnStop();              // D31: a running func measures its result at any stop
}

void Resume()
{
    if (!g_paused)
        return;
    g_paused = false;
    // After a watch, pause, frame or step stop nothing was checked at this instruction yet: check it now.
    g_skip_break_once = StopDecidedHere(g_stop_reason);
    g_skip_linear = static_cast<uint32_t>(SegPhys(cs) + reg_eip);
    g_stop_reason = "none";
}

void DisarmForLoad()
{
    Disarm();
    g_skip_break_once = false;
    // The old stop does not belong to the loaded CS:EIP: a breakpoint there must not be skipped on continue.
    if (g_paused)
        g_stop_reason = "pause";
}

bool IsPaused() { return g_paused; }

const std::string& StopReason() { return g_stop_reason; }

bool InStep() { return g_in_step; }

void ExecOnFrame()
{
    ++g_frames;
    if (!g_until.active || g_until.frames_left == 0)
        return;
    if (--g_until.frames_left == 0) {
        const std::string reason = g_until.reason;
        Disarm();
        StopAt(reason); // takes effect at the next pump point (DMCP_LoopHook)
    }
}

void UpdateHookActive()
{
    const bool needed = !g_breakpoints.empty() || !g_int_breakpoints.empty() || (g_until.active && g_until.frames_left == 0) ||
                        RecordingNeedsHook() || WatchesActive() || LeActive();
    if (!needed)
        g_skip_break_once = false; // nothing to skip: never leave the flag stale for a later breakpoint
    DMCP_hook_active = needed;
}

void RegisterExec()
{
    Register("emu.status", [](const JsonValue&, JsonValue* result, Error*) {
        *result = StatusObject();
        return true;
    });
    Register("emu.pause", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string until;
        if (Param(params, "bis") != nullptr && (!ParamStr(params, "bis", &until) || until != "pmode"))
            return Fail(error, "bad_argument", "bis must be \"pmode\" (pause at the first protected-mode instruction)");
        if (until == "pmode" && !(cpu.pmode && !GETFLAG(VM))) {
            Until u;
            u.reason = "pmode";
            u.pmode = true;
            Arm(u); // takes effect while running (now or after emu.continue)
        } else {
            Disarm();
            // A watch access made by a callback after the last instruction is still pending: report it.
            if (!g_paused)
                StopAt(WatchTakeStop() ? "watch" : until.empty() ? "pause" : "pmode");
        }
        *result = StatusObject();
        return true;
    });
    Register("emu.continue", [](const JsonValue&, JsonValue* result, Error*) {
        Resume();
        UpdateHookActive();
        *result = StatusObject();
        return true;
    });
    Register("emu.run_until", [](const JsonValue& params, JsonValue* result, Error* error) {
        Until u;
        u.reason = "run_until";
        bool flag = false;
        std::string text;
        uint64_t number = 0;
        if (Param(params, "pmode") != nullptr) {
            if (!ParamBool(params, "pmode", &flag) || !flag)
                return Fail(error, "bad_argument", "pmode must be true");
            u.pmode = true;
        }
        if (Param(params, "paging") != nullptr) {
            if (!ParamBool(params, "paging", &flag) || !flag)
                return Fail(error, "bad_argument", "paging must be true");
            u.paging = true;
        }
        if (Param(params, "cs") != nullptr) {
            uint32_t value = 0;
            if (!HexField(params, "cs", &value) || value > 0xFFFF)
                return Fail(error, "bad_argument", "cs must be a 16-bit selector or segment (hex string)");
            u.has_cs = true;
            u.cs = static_cast<uint16_t>(value);
        }
        if (Param(params, "cpl") != nullptr) {
            if (!ParamU64(params, "cpl", &number) || number > 3)
                return Fail(error, "bad_argument", "cpl must be 0..3");
            u.has_cpl = true;
            u.cpl = static_cast<uint8_t>(number);
        }
        if (Param(params, "adresse") != nullptr) {
            Address address;
            if (!ParamStr(params, "adresse", &text))
                return Fail(error, "bad_argument", "adresse must be a string");
            if (!ParseAddress(text, &address, error))
                return false;
            if (!address.has_linear)
                return Fail(error, "bad_argument", "adresse must be a code address with a linear form (not p:)");
            u.has_linear = true;
            u.linear = address.linear;
        }
        if (Param(params, "ausdruck") != nullptr) {
            if (!ParamStr(params, "ausdruck", &text))
                return Fail(error, "bad_argument", "ausdruck must be a string");
            if (!u.expr.Compile(text, error))
                return false;
        }
        if (!u.pmode && !u.paging && !u.has_cs && !u.has_cpl && !u.has_linear && u.expr.Empty())
            return Fail(error, "bad_argument", "pass at least one of adresse, pmode, paging, cs, cpl, ausdruck");
        Arm(u);
        ResumeArmed();
        UpdateHookActive();
        *result = StatusObject();
        return true;
    });
    Register("emu.eval", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string text;
        if (!ParamStr(params, "ausdruck", &text))
            return Fail(error, "bad_argument", "ausdruck (string) is required");
        Expr expr;
        if (!expr.Compile(text, error))
            return false;
        Set(result, "ausdruck", Str(text));
        Set(result, "wert", Hex(expr.Eval(), 8));
        return true;
    });
    Register("emu.step", [](const JsonValue& params, JsonValue* result, Error* error) {
        if (!g_paused)
            return Fail(error, "not_paused", "emu.step requires a paused emulator (call emu.pause first)");
        uint64_t count = 1;
        if (Param(params, "count") != nullptr && !ParamU64(params, "count", &count))
            return Fail(error, "bad_argument", "count must be a positive integer");
        if (count == 0 || count > 1000000)
            return Fail(error, "bad_argument", "count must be between 1 and 1000000");
        uint64_t done = 0;
        while (done < count) {
            g_stop_reason = "step";
            const bool ok = StepOne();
            if (WatchTakeStop())
                StopAt("watch");
            ++done;
            if (!ok || g_stop_reason == "watch")
                break;
        }
        *result = StatusObject();
        Set(result, "steps_done", Num(done));
        return true;
    });
    Register("emu.speed", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string mode;
        if (!ParamStr(params, "mode", &mode) || (mode != "normal" && mode != "turbo" && mode != "cycles"))
            return Fail(error, "bad_argument", "mode must be \"normal\", \"turbo\" or \"cycles\"");
        if (mode == "cycles") {
            uint64_t cycles = 0;
            if (!ParamU64(params, "cycles", &cycles) || cycles < 100 || cycles > 2000000)
                return Fail(error, "bad_argument", "cycles must be between 100 and 2000000");
            if (!g_speed_saved) {
                g_speed_saved = true;
                g_saved_cycles_auto = CPU_CycleAutoAdjust;
                g_saved_cycle_max = CPU_CycleMax;
                g_saved_old_cycle_max = CPU_OldCycleMax;
            }
            ticksLocked = false;
            CPU_CycleAutoAdjust = false;
            CPU_CycleMax = static_cast<cpu_cycles_count_t>(cycles);
            CPU_OldCycleMax = CPU_CycleMax;
        } else {
            if (g_speed_saved) {
                g_speed_saved = false;
                CPU_CycleAutoAdjust = g_saved_cycles_auto;
                CPU_CycleMax = g_saved_cycle_max;
                CPU_OldCycleMax = g_saved_old_cycle_max;
            }
            ticksLocked = (mode == "turbo");
        }
        *result = StatusObject();
        return true;
    });
    Register("emu.step_over", [](const JsonValue&, JsonValue* result, Error* error) {
        if (!g_paused)
            return Fail(error, "not_paused", "emu.step_over requires a paused emulator (call emu.pause first)");
        const CodePoint here = CurrentCodePoint();
        if (!IsStepOverInstruction(here.linear)) {
            g_stop_reason = "step";
            StepOne();
            if (WatchTakeStop())
                StopAt("watch");
            *result = StatusObject();
            Set(result, "art", Str("schritt"));
            return true;
        }
        const bool code32 = here.bits == 32;
        const uint32_t length = InstructionLength(here.linear, here.offset, code32);
        uint32_t next = here.offset + length;
        if (!code32)
            next &= 0xFFFFu;
        Until u;
        u.reason = "step_over";
        u.has_linear = true;
        u.linear = here.base + next;
        u.has_stack = true;
        u.stack_min = cpu.stack.big ? reg_esp : static_cast<uint32_t>(reg_sp);
        Arm(u);
        ResumeArmed();
        UpdateHookActive();
        *result = StatusObject();
        Set(result, "art", Str("laeuft"));
        Set(result, "ziel", Hex(u.linear, 8));
        return true;
    });
    Register("emu.frame_step", [](const JsonValue& params, JsonValue* result, Error* error) {
        uint64_t frames = 1;
        if (Param(params, "anzahl") != nullptr &&
            (!ParamU64(params, "anzahl", &frames) || frames == 0 || frames > 10000))
            return Fail(error, "bad_argument", "anzahl must be between 1 and 10000");
        Until u;
        u.reason = "frame";
        u.frames_left = frames;
        Arm(u);
        ResumeArmed();
        UpdateHookActive();
        *result = StatusObject();
        return true;
    });
    Register("emu.break_add", [](const JsonValue& params, JsonValue* result, Error* error) {
        Breakpoint bp;
        if (!BreakAddress(params, &bp.address, &bp.quelle, error))
            return false;
        std::string text;
        if (Param(params, "bedingung") != nullptr) {
            if (!ParamStr(params, "bedingung", &text) || !bp.bedingung.Compile(text, error)) {
                if (error->code.empty())
                    Fail(error, "bad_argument", "bedingung must be a string");
                return false;
            }
        }
        if (Param(params, "einmalig") != nullptr && !ParamBool(params, "einmalig", &bp.einmalig))
            return Fail(error, "bad_argument", "einmalig must be true or false");
        g_breakpoints[bp.address.linear] = bp;
        UpdateHookActive();
        *result = BreakpointList();
        return true;
    });
    Register("emu.break_remove", [](const JsonValue& params, JsonValue* result, Error* error) {
        bool all = false;
        bool removed = false;
        if (ParamBool(params, "all", &all) && all) {
            removed = !g_breakpoints.empty();
            g_breakpoints.clear();
        } else {
            Address address;
            std::string quelle;
            if (!BreakAddress(params, &address, &quelle, error))
                return false;
            removed = g_breakpoints.erase(address.linear) != 0;
        }
        UpdateHookActive();
        *result = BreakpointList();
        Set(result, "entfernt", Bool(removed));
        return true;
    });
    Register("emu.break_list", [](const JsonValue&, JsonValue* result, Error*) {
        *result = BreakpointList();
        return true;
    });
    Register("emu.break_int_add", [](const JsonValue& params, JsonValue* result, Error* error) {
        uint32_t nummer = 0;
        IntBreakpoint ib;
        if (!HexField(params, "nummer", &nummer) || nummer > 0xFF)
            return Fail(error, "bad_argument", "nummer must be an interrupt number 00..FF (hex string)");
        ib.nummer = static_cast<uint8_t>(nummer);
        if (!OptionalByte(params, "ah", &ib.ah, error) || !OptionalByte(params, "al", &ib.al, error))
            return false;
        bool replaced = false;
        for (IntBreakpoint& old : g_int_breakpoints) {
            if (old.nummer == ib.nummer && old.ah == ib.ah && old.al == ib.al) {
                old = ib;
                replaced = true;
            }
        }
        if (!replaced)
            g_int_breakpoints.push_back(ib);
        UpdateHookActive();
        *result = BreakpointList();
        return true;
    });
    Register("emu.break_int_remove", [](const JsonValue& params, JsonValue* result, Error* error) {
        bool all = false;
        bool removed = false;
        if (ParamBool(params, "all", &all) && all) {
            removed = !g_int_breakpoints.empty();
            g_int_breakpoints.clear();
        } else {
            uint32_t nummer = 0;
            int ah = -1, al = -1;
            if (!HexField(params, "nummer", &nummer) || nummer > 0xFF)
                return Fail(error, "bad_argument", "pass nummer (hex string) or all:true");
            if (!OptionalByte(params, "ah", &ah, error) || !OptionalByte(params, "al", &al, error))
                return false;
            for (size_t i = g_int_breakpoints.size(); i-- > 0;) {
                const IntBreakpoint& ib = g_int_breakpoints[i];
                if (ib.nummer == nummer && ib.ah == ah && ib.al == al) {
                    g_int_breakpoints.erase(g_int_breakpoints.begin() + static_cast<long>(i));
                    removed = true;
                }
            }
        }
        UpdateHookActive();
        *result = BreakpointList();
        Set(result, "entfernt", Bool(removed));
        return true;
    });
}

} // namespace dmcp

bool DMCP_LoopHook()
{
    dmcp::InputTick();
    if (!dmcp::g_paused || dmcp::g_in_step)
        return false; // nested run inside a stepped callback: let it finish
    GFX_Events();
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
    return true;
}

bool DMCP_InstructionHook()
{
    using namespace dmcp;
    const uint32_t here = static_cast<uint32_t>(SegPhys(cs) + reg_eip);
    if (WatchesActive()) {
        // An access of the previous instruction fired an "anhalten" watch: stop before this one.
        if (!g_in_step && WatchTakeStop()) {
            StopAt("watch");
            return true;
        }
        WatchOnInstruction();
    }
    // Consumed only when the instruction we resumed at is reached again (not by an IRQ handler entered first).
    const bool skip = g_skip_break_once && here == g_skip_linear;
    if (skip)
        g_skip_break_once = false;
    // During a step the nested run of a callback must not stop: the paused loop would not resume it and
    // the hook would fire on the same instruction forever.
    if (!skip && !g_in_step) {
        if (FuncActive() && FuncShouldStop()) {
            StopAt("func");
            return true;
        }
        if (g_until.active && UntilMet(here)) {
            const std::string reason = g_until.reason;
            Disarm();
            StopAt(reason);
            return true;
        }
        if (!g_breakpoints.empty()) {
            const auto it = g_breakpoints.find(here);
            if (it != g_breakpoints.end() && (it->second.bedingung.Empty() || it->second.bedingung.Eval() != 0)) {
                Breakpoint& bp = it->second;
                ++bp.treffer;
                StopAt("breakpoint");
                g_stop_linear = here;
                JsonValue event = JsonValue::Object();
                Set(&event, "art", Str("code"));
                Set(&event, "linear", HexOut(here, 8, true));
                Set(&event, "treffer", Num(bp.treffer));
                if (!bp.bedingung.Empty())
                    Set(&event, "bedingung", Str(bp.bedingung.Text()));
                EmitEvent("breakpoint", event);
                if (bp.einmalig) {
                    g_breakpoints.erase(it);
                    UpdateHookActive();
                }
                return true;
            }
        }
        if (!g_int_breakpoints.empty()) {
            uint8_t vector = 0;
            if (IntAt(here, &vector)) {
                for (IntBreakpoint& ib : g_int_breakpoints) {
                    if (ib.nummer != vector || (ib.ah >= 0 && ib.ah != reg_ah) || (ib.al >= 0 && ib.al != reg_al))
                        continue;
                    ++ib.treffer;
                    StopAt("int_breakpoint");
                    g_stop_int = vector;
                    g_stop_ax = reg_ax;
                    JsonValue event = JsonValue::Object();
                    Set(&event, "art", Str("int"));
                    Set(&event, "linear", HexOut(here, 8, true));
                    Set(&event, "nummer", HexOut(vector, 2, true));
                    Set(&event, "ax", HexOut(reg_ax, 4, true));
                    Set(&event, "treffer", Num(ib.treffer));
                    EmitEvent("breakpoint", event);
                    return true;
                }
            }
        }
    }
    RecordInstruction(); // only instructions that execute now, never the one we stop before
    return false;
}

#endif // C_DMCP
