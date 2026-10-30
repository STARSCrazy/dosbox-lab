#ifndef DOSBOX_AGENT_DMCP_H
#define DOSBOX_AGENT_DMCP_H

// dosbox-lab agent extension. Only compiled with C_DMCP (see build.ps1).
#if defined(C_DMCP)
#include <string>
#include <cstdint>

// Emulation thread. True while breakpoints, a pause condition or a recording need the per-instruction hook.
// The normal core and CPU_Interrupt test it inline, so an idle agent costs one load per instruction.
extern bool DMCP_hook_active;
// Emulation thread. True while a func run (D31) holds hardware IRQs back (checked in PIC_runIRQs).
extern bool DMCP_irq_hold;

// Transport thread: handles dosbox-lab methods. Returns false if the method is not ours.
bool DMCP_HandleRequest(const std::string& request, std::string* response);
// Emulation thread, top of Normal_Loop. true = paused, skip this loop iteration.
bool DMCP_LoopHook();
// Emulation thread, before every instruction of the normal core. true = stop before it.
bool DMCP_InstructionHook();
// true = ignore real SDL mouse and keyboard events (the agent owns the input).
bool DMCP_InputOwned();
// Emulation thread, end of RENDER_EndUpdate: a complete frame is in the source cache.
void DMCP_FrameDone();
// DOS_Execute, after a successful load. flags: 0 load+run, 1 load, 3 overlay. load_seg: DOS image segment.
// paragraphs: memory block of the program (overlay: image size). csip/sssp: entry and stack (0 for overlays).
void DMCP_OnExec(const char* name, uint16_t flags, bool com, uint16_t psp, uint16_t load_seg, uint16_t reloc,
                 uint32_t paragraphs, uint32_t csip, uint32_t sssp);
// DOS_Terminate, after the shell check: a program ends (exit_code = AL, tsr for INT 21h 31h / INT 27h).
void DMCP_OnExit(uint16_t psp, bool tsr, uint8_t exit_code);
// CPU_Interrupt, before the vector is taken (cs:eip = the INT instruction or the interrupted one).
// Only called while DMCP_hook_active.
void DMCP_OnInterrupt(unsigned num, unsigned type, uint32_t oldeip);
// DOS_WriteFile: lives for one write. While a run_batch capture is active, writes to DOS handles 1/2
// go to stdout.txt/stderr.txt; BIOS teletype output inside the write is not counted twice.
struct DMCP_DosWriteScope {
    DMCP_DosWriteScope(uint16_t entry, const uint8_t* data, uint16_t amount, bool fcb);
    ~DMCP_DosWriteScope();
    DMCP_DosWriteScope(const DMCP_DosWriteScope&) = delete;
    DMCP_DosWriteScope& operator=(const DMCP_DosWriteScope&) = delete;
};
// INT 10h AH=0Eh (teletype output) called by guest code: captured as stdout during run_batch.
void DMCP_OnBiosTty(uint8_t ch);
// End of Add_VFiles (runs on every Z: rebuild): registers Z:\DMCPBAT.COM.
void DMCP_SetupPrograms();
#endif

#endif
