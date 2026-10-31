// SPDX-License-Identifier: GPL-2.0-or-later
#ifndef DOSBOX_DMCP_MODULES_H
#define DOSBOX_DMCP_MODULES_H

#include "dmcp_addr.h"

#include <cstdint>
#include <string>
#include <vector>

namespace dmcp {

// Origin of a va mapping; the lower value wins (spec rule 3).
enum class Origin : int { SymbolsSegments = 1, SymbolsBase = 2, Set = 3, Derived = 4 };

struct ModuleSegment {
    std::string name;
    uint32_t va_start = 0;
    uint32_t va_end = 0;       // inclusive
    uint32_t base_linear = 0; // linear = base_linear + va
    Origin origin = Origin::Set;
};

struct Symbol {
    std::string name;
    uint32_t addr = 0;     // va (va32) or offset (rel16)
    uint16_t rel_seg = 0; // rel16 only
    uint32_t size = 0;    // 0 = unknown
    std::string kind;
};

// D27: LE objects of the executed file and their derived placement (dmcp_le.cpp).
struct LeObject {
    uint32_t va_start = 0; // relocation base address of the object
    uint32_t size = 0;     // virtual size
    bool assigned = false;
    uint32_t linear = 0;   // linear address of the object once assigned
};

struct LeInfo {
    bool present = false;   // the file has an "LE" header
    std::string assignment;    // "open", "done", "abandoned", "not_le"
    std::string hint;
    std::vector<LeObject> objects;
    uint32_t eip_object = 0;  // 0-based index of the entry object
    uint32_t eip = 0;         // offset inside the entry object
};

struct Module {
    std::string name;                    // upper-case file name, e.g. "PROGRAM.EXE"
    std::string file;                   // canonical DOS path from EXEC, empty if never executed
    std::string kind;                     // "exe", "com", "overlay" or "" (only mapping/symbols)
    bool has_load = false;
    uint16_t psp = 0;
    uint16_t load_seg = 0;               // DOS image segment (EXE: PSP+10h)
    uint16_t rel_base = 0;              // rel_seg = seg - rel_base (EXE: load_seg, COM: PSP)
    uint32_t paragraphs = 0;             // memory block (program) or image size (overlay)
    std::vector<ModuleSegment> segments; // va mapping, sorted by va_start, non-overlapping
    std::string address_space;              // of the loaded symbols: "va32", "rel16" or ""
    std::vector<Symbol> symbols;         // sorted by address
    uint64_t exec_count = 0;
    bool has_entry = false;              // set by EXEC 4B00/4B01 (not for overlays)
    uint32_t csip = 0;                   // entry point seg:off (high word segment)
    uint32_t sssp = 0;                   // initial stack seg:off
    std::string command_line;            // command tail at EXEC
    LeInfo le;                           // D27, only for EXEs with an LE header
};

std::vector<Module>& Modules();
Module* FindModule(const std::string& name);
Module& GetOrCreateModule(const std::string& name);
std::string Upper(std::string text);
std::string ModuleNameFromPath(const std::string& dos_path);
// Hex with optional 0x, at most 8 digits.
bool ParseHex(const std::string& text, uint32_t* out);
// [{name, va_start, va_end, base_linear}] -> segments (overlap check: SetSegments).
// default_base (optional): used for segments without their own base_linear.
bool ParseSegments(const JsonValue& list, std::vector<ModuleSegment>* out, Error* error,
                   const uint32_t* default_base = nullptr);
// Replaces the va mapping if `origin` is not weaker than the current one (*applied=false otherwise).
// Fails with bad_argument on inverted or overlapping ranges.
bool SetSegments(const std::string& module, std::vector<ModuleSegment> segments, Origin origin,
                 bool* applied, Error* error);
// Module + va for a linear address (va mapping), latest module first; nullptr if none.
const Module* ModuleForLinear(uint32_t linear, uint32_t* va);
// Module whose real-mode memory contains the segment, latest EXEC first; nullptr if none.
const Module* ModuleForRealSeg(uint16_t seg);
// Adds "module" + "va" (pm/linear) or "module" + "rel" (real/v86) to an address object.
void AnnotateAddress(const Address& address, JsonValue* object, bool file_format);
// "va:<hex>", "va:<MODULE>:<hex>", "rel:<seg>:<off>", "rel:<MODULE>:<seg>:<off>".
bool ParseModuleAddress(const std::string& text, Address* out, Error* error);
const char* OriginName(Origin origin);
// Replaces the symbols of a module and sorts them by address.
void SetSymbols(Module& module, const std::string& address_space, std::vector<Symbol> symbols);
// Address of "name" or "MODULE!name". *found=false: no module knows the name (caller reports its own error).
bool ResolveSymbol(const std::string& text, Address* out, Error* error, bool* found);
// "name" or "name+0x1A" covering key (va for va32, rel_seg*16+off for rel16); empty if none.
std::string SymbolAt(const Module& module, uint32_t key);
// Module started last with EXEC 4B00h; nullptr if none.
const Module* TargetModule();
// From DMCP_OnExec: records or updates the module of an EXEC (target=true for 4B00h).
void ModulesOnExec(const std::string& dos_path, bool overlay, bool com, uint16_t psp, uint16_t load_seg,
                   uint32_t paragraphs, bool target, uint32_t csip, uint32_t sssp, const std::string& command_line);

// dmcp_le.cpp: the "le" object of modules.list.
JsonValue LeJson(const LeInfo& le);

} // namespace dmcp

#endif
