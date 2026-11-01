// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "cpu.h"
#include "mem.h"
#include "paging.h"
#include "regs.h"
#include "dmcp_addr.h"
#include "dmcp_modules.h"

#include <cctype>
#include <cstdlib>
#include <cstring>

namespace dmcp {
namespace {

bool ParseHex32(const std::string& text, uint32_t* out)
{
    if (text.empty() || text.size() > 8)
        return false;
    for (char c : text)
        if (!std::isxdigit(static_cast<unsigned char>(c)))
            return false;
    *out = static_cast<uint32_t>(std::strtoul(text.c_str(), nullptr, 16));
    return true;
}

bool PagingOn() { return (cpu.cr0 & 0x80000000u) != 0; }

void AddMissing(RangeList* missing, uint32_t start, uint32_t end)
{
    if (!missing->empty() && missing->back().second == start)
        missing->back().second = end;
    else
        missing->push_back(std::make_pair(start, end));
}

void ReadLinear(uint32_t linear, uint8_t* out, size_t len, RangeList* missing)
{
    size_t done = 0;
    while (done < len) {
        const uint32_t current = linear + static_cast<uint32_t>(done);
        const size_t in_page = 0x1000u - (current & 0xFFFu);
        const size_t chunk = in_page < len - done ? in_page : len - done;
        uint32_t phys = 0;
        Error ignored;
        HostPt host = nullptr;
        if (LinearToPhys(current, &phys, &ignored) && (host = PhysHostPtr(phys, false)) != nullptr) {
            std::memcpy(out + done, host, chunk); // chunk stays inside one page
        } else {
            std::memset(out + done, 0, chunk);
            AddMissing(missing, current, current + static_cast<uint32_t>(chunk));
        }
        done += chunk;
    }
}

} // namespace

uint64_t GuestRamBytes()
{
    return static_cast<uint64_t>(MEM_TotalPages()) << 12;
}

bool PhysInRam(uint32_t phys, size_t len)
{
    return static_cast<uint64_t>(phys) + len <= GuestRamBytes();
}

uint8_t* PhysHostPtr(uint32_t phys, bool write)
{
    const Bitu page = phys >> 12;
    PageHandler* handler = MEM_GetPageHandler(page);
    if (handler == nullptr)
        return nullptr;
    const Bitu flags = handler->getFlags();
    if ((flags & PFLAG_READABLE) == 0)
        return nullptr;
    // ROM pages are not PFLAG_WRITEABLE; the BIOS ROM inside guest RAM (F0000-FFFFF) may still be patched like RAM,
    // the ROM aliases at the top of the address space may not.
    if (write && (flags & PFLAG_WRITEABLE) == 0 && !((flags & PFLAG_HASROM) != 0 && PhysInRam(phys, 1)))
        return nullptr;
    HostPt base = (write && (flags & PFLAG_WRITEABLE) != 0) ? handler->GetHostWritePt(page)
                                                             : handler->GetHostReadPt(page);
    return base != nullptr ? base + (phys & 0xFFFu) : nullptr;
}

bool LinearToPhys(uint32_t linear, uint32_t* phys, Error* error)
{
    if (!PagingOn()) {
        // Below 1 MB + HMA DOSBox remaps pages without paging (EMS page frame): follow that mapping.
        const uint32_t page = linear >> 12;
        *phys = page < LINK_START ? ((paging.firstmb[page] << 12) | (linear & 0xFFFu)) : linear;
        return true;
    }
    const uint32_t dir = static_cast<uint32_t>(PAGING_GetDirBase()) & 0xFFFFF000u;
    if (!PhysInRam(dir, 0x1000))
        return Fail(error, "page_not_present", "page directory lies outside guest RAM");
    const uint32_t pde = phys_readd(static_cast<PhysPt>(dir + ((linear >> 22) << 2)));
    if ((pde & 1u) == 0)
        return Fail(error, "page_not_present", "page directory entry not present");
    if ((pde & 0x80u) != 0 && (cpu.cr4 & 0x10u) != 0) { // 4 MB page (PSE)
        *phys = (pde & 0xFFC00000u) | (linear & 0x003FFFFFu);
        return true;
    }
    if (!PhysInRam(pde & 0xFFFFF000u, 0x1000))
        return Fail(error, "page_not_present", "page table lies outside guest RAM");
    const uint32_t pte = phys_readd(static_cast<PhysPt>((pde & 0xFFFFF000u) + (((linear >> 12) & 0x3FFu) << 2)));
    if ((pte & 1u) == 0)
        return Fail(error, "page_not_present", "page table entry not present");
    *phys = (pte & 0xFFFFF000u) | (linear & 0xFFFu);
    return true;
}

bool LoadDescriptor(uint16_t selector, Descriptor* desc, Error* error)
{
    if (!cpu.pmode)
        return Fail(error, "bad_address", "selectors need protected mode");
    const uint32_t index = selector & 0xFFF8u;
    uint32_t table_base = 0;
    uint32_t table_limit = 0;
    if ((selector & 4u) != 0) {
        const uint16_t ldt_selector = static_cast<uint16_t>(cpu.gdt.SLDT());
        if ((ldt_selector & 0xFFF8u) == 0)
            return Fail(error, "bad_address", "selector refers to the LDT, but no LDT is loaded");
        Descriptor ldt;
        uint8_t raw[8];
        RangeList missing;
        ReadLinear(static_cast<uint32_t>(cpu.gdt.GetBase()) + (ldt_selector & 0xFFF8u), raw, 8, &missing);
        if (!missing.empty())
            return Fail(error, "page_not_present", "GDT entry of the LDT is not mapped");
        std::memcpy(&ldt.saved.fill[0], raw, 4);
        std::memcpy(&ldt.saved.fill[1], raw + 4, 4);
        table_base = static_cast<uint32_t>(ldt.GetBase());
        table_limit = static_cast<uint32_t>(ldt.GetLimit());
    } else {
        if (index == 0)
            return Fail(error, "bad_address", "null selector");
        table_base = static_cast<uint32_t>(cpu.gdt.GetBase());
        table_limit = static_cast<uint32_t>(cpu.gdt.GetLimit());
    }
    if (index + 7u > table_limit)
        return Fail(error, "bad_address", "selector outside descriptor table");
    uint8_t raw[8];
    RangeList missing;
    ReadLinear(table_base + index, raw, 8, &missing);
    if (!missing.empty())
        return Fail(error, "page_not_present", "descriptor table page is not mapped");
    std::memcpy(&desc->saved.fill[0], raw, 4);
    std::memcpy(&desc->saved.fill[1], raw + 4, 4);
    return true;
}

bool ParseAddress(const std::string& text, Address* out, Error* error)
{
    Address a;
    if (text.compare(0, 2, "l:") == 0) {
        if (!ParseHex32(text.substr(2), &a.linear))
            return Fail(error, "bad_argument", "bad linear address: " + text);
        a.mode = "linear";
        a.has_linear = true;
        ResolvePhys(&a);
        *out = a;
        return true;
    }
    if (text.compare(0, 2, "p:") == 0) {
        if (!ParseHex32(text.substr(2), &a.phys))
            return Fail(error, "bad_argument", "bad physical address: " + text);
        a.mode = "phys";
        a.has_phys = true;
        *out = a;
        return true;
    }
    if (text.compare(0, 3, "va:") == 0 || text.compare(0, 4, "rel:") == 0)
        return ParseModuleAddress(text, out, error);

    bool force_real = false;
    std::string rest = text;
    if (rest.compare(0, 2, "r:") == 0) {
        force_real = true;
        rest = rest.substr(2);
    }
    const size_t colon = rest.find(':');
    if (colon == std::string::npos && !force_real) {
        bool found = false;
        if (ResolveSymbol(text, out, error, &found))
            return true;
        if (found)
            return false;
    }
    uint32_t selector = 0;
    uint32_t offset = 0;
    if (colon == std::string::npos || colon > 4 || !ParseHex32(rest.substr(0, colon), &selector) ||
        !ParseHex32(rest.substr(colon + 1), &offset))
        return Fail(error, "bad_argument",
                    "address must be SSSS:OOOOOOOO, r:SSSS:OOOO, l:<hex>, p:<hex>, va:..., rel:... or a known symbol "
                    "name (got \"" + text + "\")");
    a.has_selector = true;
    a.selector = static_cast<uint16_t>(selector);
    a.offset = offset;
    if (force_real || !cpu.pmode || GETFLAG(VM)) {
        if (offset > 0xFFFFu && !force_real && !cpu.pmode)
            return Fail(error, "bad_argument",
                        "CPU is in real mode (e.g. DOS extender in a DOS call); selector addresses need protected "
                        "mode - pause again until emu.status pmode=true, or use l:<linear>");
        if (offset > 0xFFFFu)
            return Fail(error, "bad_argument", force_real ? "real-mode offset must be <= FFFF"
                                                          : "CPU is in v86 mode; offset must be <= FFFF");
        a.mode = force_real || !cpu.pmode ? "real" : "v86";
        a.base = selector << 4;
        a.bits = 16;
    } else {
        Descriptor desc;
        if (!LoadDescriptor(a.selector, &desc, error))
            return false;
        if (desc.saved.seg.p == 0)
            return Fail(error, "bad_address", "segment not present");
        if (!desc.GetExpandDown() && offset > desc.GetLimit())
            return Fail(error, "bad_address", "offset beyond segment limit");
        a.mode = "pm";
        a.base = static_cast<uint32_t>(desc.GetBase());
        a.bits = desc.Big() ? 32 : 16;
    }
    a.has_linear = true;
    a.linear = a.base + offset;
    ResolvePhys(&a);
    *out = a;
    return true;
}

void ResolvePhys(Address* address)
{
    if (!address->has_linear)
        return;
    Error ignored;
    uint32_t phys = 0;
    address->has_phys = LinearToPhys(address->linear, &phys, &ignored);
    address->phys = phys;
}

JsonValue AddressJson(const Address& a, bool file_format)
{
    JsonValue result = JsonValue::Object();
    Set(&result, "mode", Str(a.mode));
    if (a.has_selector) {
        Set(&result, a.mode == "pm" ? "sel" : "seg", HexOut(a.selector, 4, file_format));
        Set(&result, "off", HexOut(a.offset, a.mode == "pm" ? 8 : 4, file_format));
        Set(&result, "base", HexOut(a.base, 8, file_format));
    }
    if (a.has_linear)
        Set(&result, "linear", HexOut(a.linear, 8, file_format));
    if (a.has_phys)
        Set(&result, "phys", HexOut(a.phys, 8, file_format));
    if (a.bits != 0)
        Set(&result, "bits", Num(static_cast<uint64_t>(a.bits)));
    AnnotateAddress(a, &result, file_format);
    return result;
}

void ReadAddress(const Address& address, uint8_t* out, size_t len, RangeList* missing)
{
    if (!address.has_linear) { // physical: bytes that are not plain memory read as 0 and are listed as missing
        size_t done = 0;
        while (done < len) {
            const uint64_t phys = static_cast<uint64_t>(address.phys) + done;
            const size_t in_page = 0x1000u - static_cast<size_t>(phys & 0xFFFu);
            const size_t chunk = in_page < len - done ? in_page : len - done;
            HostPt host = phys <= 0xFFFFFFFFull ? PhysHostPtr(static_cast<uint32_t>(phys), false) : nullptr;
            if (host != nullptr) {
                std::memcpy(out + done, host, chunk);
            } else {
                std::memset(out + done, 0, chunk);
                AddMissing(missing, static_cast<uint32_t>(phys), static_cast<uint32_t>(phys + chunk));
            }
            done += chunk;
        }
        return;
    }
    ReadLinear(address.linear, out, len, missing);
}

bool WriteAddress(const Address& address, const uint8_t* data, size_t len, Error* error)
{
    std::vector<HostPt> host(len);
    if (!address.has_linear) {
        for (size_t i = 0; i < len; ++i) {
            const uint64_t phys = static_cast<uint64_t>(address.phys) + i;
            host[i] = phys <= 0xFFFFFFFFull ? PhysHostPtr(static_cast<uint32_t>(phys), true) : nullptr;
            if (host[i] == nullptr)
                return Fail(error, "bad_address", "physical range lies (partly) outside guest memory");
        }
    } else {
        for (size_t i = 0; i < len; ++i) {
            uint32_t phys = 0;
            if (!LinearToPhys(address.linear + static_cast<uint32_t>(i), &phys, error))
                return false;
            host[i] = PhysHostPtr(phys, true);
            if (host[i] == nullptr)
                return Fail(error, "bad_address", "target byte lies outside guest memory");
        }
    }
    for (size_t i = 0; i < len; ++i)
        *host[i] = data[i];
    return true;
}

JsonValue RangesJson(const RangeList& ranges, bool file_format)
{
    JsonValue list = JsonValue::Array();
    for (const auto& range : ranges) {
        JsonValue pair = JsonValue::Array();
        pair.array.push_back(HexOut(range.first, 8, file_format));
        pair.array.push_back(HexOut(range.second, 8, file_format));
        list.array.push_back(pair);
    }
    return list;
}

Address Offset(const Address& address, uint32_t delta)
{
    Address shifted = address;
    shifted.offset += delta;
    if (shifted.has_linear)
        shifted.linear += delta;
    if (!shifted.has_linear)
        shifted.phys += delta;
    else
        ResolvePhys(&shifted);
    return shifted;
}

CodePoint CurrentCodePoint()
{
    CodePoint p;
    p.selector = static_cast<uint16_t>(SegValue(cs));
    p.base = static_cast<uint32_t>(SegPhys(cs));
    p.offset = reg_eip;
    p.linear = p.base + reg_eip;
    p.bits = cpu.code.big ? 32 : 16;
    p.mode = !cpu.pmode ? 0 : (GETFLAG(VM) ? 1 : 2);
    return p;
}

Address CodePointAddress(const CodePoint& p)
{
    static const char* const kMode[] = {"real", "v86", "pm"};
    Address a;
    a.mode = kMode[p.mode];
    a.has_selector = true;
    a.selector = p.selector;
    a.offset = p.offset;
    a.base = p.base;
    a.has_linear = true;
    a.linear = p.linear;
    a.bits = p.bits;
    return a;
}

Address SegCacheAddress(int seg, uint32_t offset)
{
    const SegNames index = static_cast<SegNames>(seg);
    Address a;
    a.mode = !cpu.pmode ? "real" : (GETFLAG(VM) ? "v86" : "pm");
    a.has_selector = true;
    a.selector = static_cast<uint16_t>(SegValue(index));
    a.offset = offset;
    a.base = static_cast<uint32_t>(SegPhys(index));
    a.has_linear = true;
    a.linear = a.base + offset;
    if (index == cs)
        a.bits = cpu.code.big ? 32 : 16;
    ResolvePhys(&a);
    return a;
}

bool PeekLinear(uint32_t linear, uint8_t* out)
{
    const HostPt tlb = get_tlb_read(linear);
    if (tlb != nullptr) {
        *out = host_readb(tlb + linear);
        return true;
    }
    uint32_t phys = 0;
    Error ignored;
    HostPt host = nullptr;
    if (!LinearToPhys(linear, &phys, &ignored) || (host = PhysHostPtr(phys, false)) == nullptr)
        return false;
    *out = *host;
    return true;
}

} // namespace dmcp

#endif // C_DMCP
