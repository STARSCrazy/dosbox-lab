#ifndef DOSBOX_DMCP_DISASM_H
#define DOSBOX_DMCP_DISASM_H

#include <cstdint>

namespace dmcp {

// Length of the instruction at linear (offset = eip for relative targets), at least 1. Uses DasmI386.
uint32_t InstructionLength(uint32_t linear, uint32_t offset, bool code32);
// CALL (E8, 9A, FF /2, FF /3), INT n / INT3 / INTO, or a REP/REPNE string instruction (emu.step_over).
bool IsStepOverInstruction(uint32_t linear);

} // namespace dmcp

#endif
