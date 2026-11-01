// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "cpu.h"
#include "regs.h"
#include "agent/dmcp.h"
#include "dmcp_internal.h"
#include "dmcp_record.h"

namespace dmcp {

// Per-instruction and per-interrupt dispatcher of the recordings. The recording tasks
// (events, ints, mode, coverage, flow) add their calls here.
bool RecordingNeedsHook()
{
    return EventsActive() || CoverageActive() || FlowEngineActive() || FuncActive();
}

void RecordInstruction()
{
    const uint32_t linear = static_cast<uint32_t>(SegPhys(cs) + reg_eip);
    if (CoverageActive())
        CoverageOnInstruction(linear);
    if (CallsActive())
        CallsOnInstruction();
    if (FlowEngineActive())
        FlowOnInstruction(linear);
    if (EventsActive())
        ModeOnInstruction();
    if (IntsOpen())
        IntsOnInstruction();
    if (FileIoOpen())
        FileIoOnInstruction();
    if (LeActive())
        LeOnInstruction();
    if (FuncActive())
        FuncOnInstruction();
}

} // namespace dmcp

void DMCP_OnInterrupt(unsigned num, unsigned type, uint32_t oldeip)
{
    if (dmcp::FlowEngineActive())
        dmcp::FlowOnInterrupt(static_cast<uint8_t>(num), type);
    if ((type & CPU_INT_SOFTWARE) != 0 && dmcp::EventsActive())
        dmcp::IntsOnSoftwareInterrupt(static_cast<uint8_t>(num), oldeip);
    if ((type & CPU_INT_SOFTWARE) != 0 && dmcp::EventsActive())
        dmcp::FileIoOnSoftwareInterrupt(static_cast<uint8_t>(num), oldeip);
    if ((type & CPU_INT_SOFTWARE) != 0 && dmcp::FuncActive())
        dmcp::FuncOnSoftwareInterrupt(static_cast<uint8_t>(num), oldeip);
    if ((type & CPU_INT_SOFTWARE) != 0 && dmcp::LeActive())
        dmcp::LeOnSoftwareInterrupt(static_cast<uint8_t>(num), oldeip);
}

#endif // C_DMCP
