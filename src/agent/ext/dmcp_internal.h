// SPDX-License-Identifier: GPL-2.0-or-later
#ifndef DOSBOX_DMCP_INTERNAL_H
#define DOSBOX_DMCP_INTERNAL_H

#include "agent/agent_protocol.h"

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace dmcp {

using dosbox_agent::JsonType;
using dosbox_agent::JsonValue;

struct Error {
    std::string code;     // see Global Constraints of the stage-1 plan
    std::string message;
};

typedef std::function<bool(const JsonValue& params, JsonValue* result, Error* error)> Handler;

// on_emulation_thread=true: the handler runs on the emulation thread between two instructions.
void Register(const std::string& method, Handler handler, bool on_emulation_thread = true);

JsonValue Str(const std::string& value);
JsonValue Num(uint64_t value);
JsonValue NumF(double value);
JsonValue Bool(bool value);
JsonValue Hex(uint64_t value, int digits);
// file_format=true: "0x" prefix (recording files for external tools); false: plain like all RPC answers.
JsonValue HexOut(uint64_t value, int digits, bool file_format);
// Fixed-point number with 3 decimals (emulated milliseconds).
JsonValue NumFixed(double value);
// Reads a 32-bit value from object[name]: a string is hex (optional 0x), a number is decimal.
bool HexField(const JsonValue& object, const std::string& name, uint32_t* out);
void Set(JsonValue* object, const std::string& name, const JsonValue& value);

const JsonValue* Param(const JsonValue& params, const std::string& name);
bool ParamStr(const JsonValue& params, const std::string& name, std::string* out);
// Accepts a JSON number or a string like "0x1A" / "26".
bool ParamU64(const JsonValue& params, const std::string& name, uint64_t* out);
bool ParamBool(const JsonValue& params, const std::string& name, bool* out);
// Fills *error and returns false, so handlers can write `return Fail(...)`.
bool Fail(Error* error, const std::string& code, const std::string& message);

// Output files go to %DMCP_OUTDIR% (set by the Python layer), else the current directory.
std::string OutputPath(const std::string& file_name);
bool WriteOutputFile(const std::string& file_name, const void* data, size_t size,
                     std::string* full_path, Error* error);

void RegisterBase();
void RegisterExec();
void RegisterCpu();
void RegisterMem();
void RegisterInput();
void RegisterModules();
void RegisterSymbols();
void RegisterEvents();
void RegisterCoverage();
void RegisterFlow();
void RegisterCalls();
// dmcp_func.cpp (D31): run one recorded call again.
void RegisterFunc();
// dmcp_exec.cpp: stop_reason of the current pause ("none" while running).
const std::string& StopReason();
// dmcp_cpu.cpp: eax..edi, ebp, esp, eip by name; nullptr otherwise.
uint32_t* GeneralRegister(const std::string& name);
// dmcp_mem.cpp: non-empty even-length hex text to bytes.
bool FromHex(const std::string& text, std::vector<uint8_t>* out);

// Emulation thread: delivers due queued key events (called from DMCP_LoopHook).
void InputTick();
bool InputOwned();
void RegisterScreen();

// dmcp_exec.cpp: recompute DMCP_hook_active after any change of breakpoints, pause conditions or recordings.
void UpdateHookActive();
// dmcp_exec.cpp: pause with the given stop_reason (clears the skip flag); resume like emu.continue.
void StopAt(const std::string& reason);
void Resume();
// dmcp_exec.cpp: state.load drops the shared stop condition (run_until/step_over/frame_step/pause until=pmode).
void DisarmForLoad();
bool IsPaused();
// dmcp_exec.cpp: true while emu.step / emu.step_over executes one instruction (paused, but guest code runs).
bool InStep();
// dmcp_screen.cpp calls this for every finished frame (emu.frame_step).
void ExecOnFrame();
// dmcp_record.cpp: true while any recording needs the per-instruction hook.
bool RecordingNeedsHook();
// dmcp_record.cpp: called for every instruction that executes now (not for one the agent stops before).
void RecordInstruction();

// While > 0, memory accesses made by the agent itself (disassembler) do not trigger watches (dmcp_watch.cpp).
int& QuietDepth();
struct QuietMem {
    QuietMem() { ++QuietDepth(); }
    ~QuietMem() { --QuietDepth(); }
    QuietMem(const QuietMem&) = delete;
    QuietMem& operator=(const QuietMem&) = delete;
};
void RegisterDisasm();
void RegisterWatch();
void RegisterState();
void RegisterDos();
// dmcp_batch.cpp: run_batch capture (DMCPBAT.COM, batch.status, batch.json); BatchOnExec counts started programs.
void RegisterBatch();
void BatchOnExec(const std::string& path, bool load_and_run);

// dmcp_le.cpp (D27): derive the linear placement of LE objects from DPMI 0501h allocations.
bool LeActive(); // a derivation is open: the instruction hook must stay on
void LeOnExec(const char* raw_name, const std::string& module_name, uint16_t flags);
// Observation: INT 31h AX=0501h entry (hook), its return (instruction hook), end of the program.
void LeOnSoftwareInterrupt(uint8_t num, uint32_t oldeip);
void LeOnInstruction();
void LeOnExit(uint16_t psp);
// DMCP_OnExit: a program ended during the capture (errorlevel for batch.json / batch.status last_exit_code).
void BatchOnExit(uint16_t psp, uint8_t exit_code);

} // namespace dmcp

#endif
