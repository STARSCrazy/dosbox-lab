#ifndef DOSBOX_DMCP_ADDR_H
#define DOSBOX_DMCP_ADDR_H

#include "dmcp_internal.h"

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

class Descriptor;

namespace dmcp {

typedef std::vector<std::pair<uint32_t, uint32_t>> RangeList; // [start, end); physical for p: addresses

struct Address {
    std::string modus;          // "real", "v86", "pm", "linear", "phys"
    bool has_selector = false;  // seg (real/v86) or selector (pm)
    uint16_t selector = 0;
    uint32_t offset = 0;
    uint32_t base = 0;
    bool has_linear = false;
    uint32_t linear = 0;
    bool has_phys = false;
    uint32_t phys = 0;
    int bits = 0;               // 16/32 if known
};

// Accepts "SSSS:OOOOOOOO" (current CPU mode), "r:SSSS:OOOO" (forced real mode),
// "l:<hex>" (linear), "p:<hex>" (physical).
bool ParseAddress(const std::string& text, Address* out, Error* error);
// Size of guest RAM in bytes (MEM_TotalPages() << 12). Physical addresses at or above it read as FF in
// DOSBox-X, so the agent treats them as missing instead.
uint64_t GuestRamBytes();
// True if [phys, phys+len) lies completely inside guest RAM.
bool PhysInRam(uint32_t phys, size_t len);
// Walks the guest page tables (no TLB). Fails with page_not_present if unmapped or if a page
// directory/table lies outside guest RAM. Does not check that the resulting page is inside RAM.
bool LinearToPhys(uint32_t linear, uint32_t* phys, Error* error);
// Fills has_phys/phys if the linear address is mapped; never fails.
void ResolvePhys(Address* address);

// One byte without side effects: TLB host pointer if present, else own page walk + raw RAM.
// false if unmapped or outside guest RAM. Never raises guest faults and never triggers watches.
bool PeekLinear(uint32_t linear, uint8_t* out);
JsonValue AddressJson(const Address& address, bool file_format = false);
// Reads len bytes starting at the address. Unmapped pages and bytes outside guest RAM read as 0 and are
// listed in *missing (linear ranges for linear/segmented addresses, physical ranges for p:).
void ReadAddress(const Address& address, uint8_t* out, size_t len, RangeList* missing);
// Writes; fails (nothing written) with page_not_present if any page is unmapped, or with bad_address if
// any target byte lies outside guest RAM.
bool WriteAddress(const Address& address, const uint8_t* data, size_t len, Error* error);
// Reads a GDT/LDT descriptor without guest faults.
bool LoadDescriptor(uint16_t selector, Descriptor* desc, Error* error);
JsonValue RangesJson(const RangeList& ranges, bool file_format = false);
// Address shifted by delta bytes (keeps mode; selector/offset form shifts the offset).
Address Offset(const Address& address, uint32_t delta);

// Compact code address, recorded on control-flow changes. No phys: paging may change before it is saved.
struct CodePoint {
    uint32_t linear = 0;
    uint32_t offset = 0;
    uint32_t base = 0;
    uint16_t selector = 0;
    uint8_t bits = 16;
    uint8_t modus = 0; // 0 real, 1 v86, 2 pm
};
// cs:eip from the CPU segment cache (no descriptor-table read; cheap enough for every instruction).
CodePoint CurrentCodePoint();
// The point as Address (without phys).
Address CodePointAddress(const CodePoint& point);
// seg (a SegNames value) + offset from the segment cache, with phys if mapped.
Address SegCacheAddress(int seg, uint32_t offset);

} // namespace dmcp

#endif
