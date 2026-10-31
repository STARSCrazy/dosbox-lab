// SPDX-License-Identifier: GPL-2.0-or-later
#ifndef DOSBOX_DMCP_RECORD_H
#define DOSBOX_DMCP_RECORD_H

#include "dmcp_addr.h"
#include "dmcp_internal.h"

#include <cstdint>
#include <string>

namespace dmcp {

// --- dmcp_events.cpp ---
struct EventTypes {
    bool exec = true, exit = true, int_ = true, int_ret = true, mode = true, new_code_selector = true, file = true;
};
bool EventsActive();
// Which event types are switched on; only meaningful while EventsActive().
const EventTypes& ActiveEventTypes();
// Incremented by every events.start; recording parts compare it to reset their state lazily.
uint64_t EventsGeneration();
// Appends one event (no-op if events are off or the type is filtered). Adds nr, type, time_ms and,
// if the object has no "addr", the current cs:eip.
void EmitEvent(const std::string& type, JsonValue event);
uint64_t LastEventNr();
// int log filter: number/AH/AX rules and int_mode. pm=false for real and v86 mode.
bool IntWanted(uint8_t num, uint16_t ax, bool pm);
// events.start with_return: int_ret for every logged interrupt, not only INT 31h.
bool IntReturnsWanted();
// events.start file_preview: bytes of the transferred data in file events (0 = none).
uint32_t FilePreview();
// A recording hit its size limit: writes a limit_reached event if events are on.
void ReportLimit(const std::string& recording, const std::string& detail);

// From DMCP_OnExec (dmcp_modules.cpp).
void EventOnExec(const std::string& path, uint16_t flags, bool com, uint16_t psp, uint16_t load_seg,
                 uint16_t reloc, uint32_t paragraphs, uint32_t csip, uint32_t sssp);

// --- dmcp_ints.cpp ---
// CPU_Interrupt with CPU_INT_SOFTWARE: logs the int (filter), remembers it until its return address runs.
void IntsOnSoftwareInterrupt(uint8_t num, uint32_t oldeip);
bool IntsOpen();
// Per instruction while interrupts are open: emits int_ret when an open interrupt returns.
void IntsOnInstruction();
// A program ends: forget its open interrupts (they never return) and all protected-mode ones.
void IntsOnExit(uint16_t psp);

// --- dmcp_fileio.cpp (D34) ---
// CPU_Interrupt with CPU_INT_SOFTWARE: INT 21h 3Fh/40h on files, emitted as file when they return.
void FileIoOnSoftwareInterrupt(uint8_t num, uint32_t oldeip);
bool FileIoOpen();
void FileIoOnInstruction();
void FileIoOnExit(uint16_t psp);
void FileIoOnStateLoad(); // open accesses of the replaced state never return

// --- dmcp_mode.cpp ---
// Per instruction while events are on: mode and new_code_selector events.
void ModeOnInstruction();

// --- dmcp_coverage.cpp ---
bool CoverageActive();
void CoverageOnInstruction(uint32_t linear);
// Basic-block hit counters (coverage.start hits=true); fed by the control-flow engine.
bool CoverageHitsOn();
void CoverageHit(uint32_t linear);

// --- dmcp_flow.cpp ---
// Decoder runs while flow recording or coverage hit counters are on.
bool FlowEngineActive();
void FlowResetIfIdle(); // drops a stale pending branch when no flow/coverage-hits consumer is left
void FlowOnStateLoad(); // drops the pending transfer of the replaced state
void FlowOnInstruction(uint32_t linear);
void FlowOnInterrupt(uint8_t num, unsigned type);

// --- dmcp_calls.cpp (D29) ---
enum class TransferKind : uint8_t { CallNear, CallFar, Int, Irq, Exception, Ret, Retf, Iret };
struct Transfer {
    TransferKind kind;
    uint8_t vector;      // Int/Irq/Exception
    CodePoint src;       // instruction that caused the transfer (Irq/Exception: interrupted instruction)
    CodePoint dst;       // first instruction after the transfer
    uint16_t ss_before;  // SS before the transfer
    uint32_t sp_before;  // ESP (32-bit stack) or SP (16-bit stack) before the transfer
};
bool CallsActive();
void CallsOnInstruction();               // counts instructions; called before FlowOnInstruction
void CallsOnTransfer(const Transfer& t); // every taken call/int/irq/exception/ret/retf/iret
void CallsOnExit(uint16_t psp);
void CallsOnStateLoad();
uint64_t CallsCurrentId();               // innermost open recorded call of the current context, 0 = none

// --- dmcp_func.cpp (D31) ---
bool FuncActive();                    // a func run is going on (hook needed)
bool FuncShouldStop();                // instruction hook, before the stop checks: true = stop before this instruction
void FuncOnInstruction();             // an instruction executes now (count, lowest stack, int returns)
void FuncOnSoftwareInterrupt(uint8_t num, uint32_t oldeip);
void FuncOnStop();                    // StopAt: the run ends here, measure the result
void FuncOnExit(uint16_t psp);        // program end during a run
void FuncOnStateLoad();               // state.load during a run

} // namespace dmcp

#endif
