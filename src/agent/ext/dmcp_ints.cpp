// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "callback.h"
#include "cpu.h"
#include "mem.h"
#include "regs.h"
#include "dmcp_addr.h"
#include "dmcp_record.h"

#include <cstdio>
#include <vector>

Bitu FillFlags(void);

namespace dmcp {
namespace {

struct OpenInt {
    uint64_t nr = 0;
    uint8_t num = 0;
    bool pm = false;
    uint16_t cs = 0;
    uint32_t ret_eip = 0;
    uint16_t ax = 0;
    bool want_return = false;
};

const size_t kMaxOpen = 64;
std::vector<OpenInt> g_open;
uint64_t g_generation = 0;

struct DpmiName {
    uint16_t ax;
    const char* name;
};

const DpmiName kDpmi[] = {
    {0x0000, "ldt_descriptors_allocate"}, {0x0001, "ldt_descriptor_free"}, {0x0002, "segment_to_descriptor"},
    {0x0003, "selector_increment"},    {0x0006, "segment_base_get"},       {0x0007, "segment_base_set"},
    {0x0008, "segment_limit_set"},      {0x0009, "access_rights_set"},    {0x000A, "alias_descriptor"},
    {0x000B, "descriptor_get"},         {0x000C, "descriptor_set"},        {0x0100, "dos_memory_allocate"},
    {0x0101, "dos_memory_free"},   {0x0200, "rm_vector_get"},          {0x0201, "rm_vector_set"},
    {0x0204, "pm_vector_get"},          {0x0205, "pm_vector_set"},         {0x0300, "rm_interrupt_simulate"},
    {0x0301, "rm_far_call"},              {0x0400, "version"},                  {0x0500, "free_memory"},
    {0x0501, "memory_allocate"},         {0x0502, "memory_free"},       {0x0503, "memory_resize"},
    {0x0600, "region_lock"},          {0x0601, "region_unlock"},       {0x0800, "physical_map"},
};

bool InPm() { return cpu.pmode && !GETFLAG(VM); }

void CheckGeneration()
{
    if (g_generation != EventsGeneration()) {
        g_open.clear();
        g_generation = EventsGeneration();
    }
}

uint32_t Pair(uint16_t high, uint16_t low) { return (static_cast<uint32_t>(high) << 16) | low; }

std::string SegOffText(uint16_t seg, uint32_t off, int off_digits)
{
    char text[24];
    snprintf(text, sizeof(text), "%04X:%0*X", static_cast<unsigned>(seg), off_digits, static_cast<unsigned>(off));
    return text;
}

// String at seg:offset without guest faults. terminator 0 = ASCIIZ, '$' = INT 21h 09h.
std::string ReadString(int seg, uint32_t offset, char terminator, size_t max_len)
{
    std::vector<uint8_t> raw(max_len);
    RangeList missing;
    ReadAddress(SegCacheAddress(seg, offset), raw.data(), raw.size(), &missing);
    std::string out;
    for (uint8_t c : raw) {
        if (c == static_cast<uint8_t>(terminator))
            break;
        const bool keep = (c >= 0x20 && c < 0x7F) || c == '\r' || c == '\n' || c == '\t';
        out.push_back(keep ? static_cast<char>(c) : '?');
    }
    return out;
}

JsonValue RegistersJson()
{
    JsonValue r = JsonValue::Object();
    Set(&r, "eax", HexOut(reg_eax, 8, true));
    Set(&r, "ebx", HexOut(reg_ebx, 8, true));
    Set(&r, "ecx", HexOut(reg_ecx, 8, true));
    Set(&r, "edx", HexOut(reg_edx, 8, true));
    Set(&r, "esi", HexOut(reg_esi, 8, true));
    Set(&r, "edi", HexOut(reg_edi, 8, true));
    Set(&r, "ebp", HexOut(reg_ebp, 8, true));
    Set(&r, "esp", HexOut(reg_esp, 8, true));
    Set(&r, "ds", HexOut(SegValue(ds), 4, true));
    Set(&r, "es", HexOut(SegValue(es), 4, true));
    Set(&r, "fs", HexOut(SegValue(fs), 4, true));
    Set(&r, "gs", HexOut(SegValue(gs), 4, true));
    Set(&r, "ss", HexOut(SegValue(ss), 4, true));
    Set(&r, "eflags", HexOut(static_cast<uint32_t>(reg_flags), 8, true));
    return r;
}

void AddStrings(uint8_t num, JsonValue* event)
{
    if (num != 0x21)
        return;
    const bool wide = InPm() && cpu.code.big; // 32-bit PM clients pass EDX/ESI/EDI
    const uint32_t dx = wide ? reg_edx : reg_dx;
    switch (reg_ah) {
    case 0x09:
        Set(event, "text", Str(ReadString(ds, dx, '$', 256)));
        break;
    case 0x39: case 0x3A: case 0x3B: case 0x3C: case 0x3D: case 0x41: case 0x43: case 0x4B: case 0x4E: case 0x5B:
        Set(event, "filename", Str(ReadString(ds, dx, 0, 128)));
        break;
    case 0x56:
        Set(event, "filename", Str(ReadString(ds, dx, 0, 128)));
        Set(event, "new_name", Str(ReadString(es, wide ? reg_edi : reg_di, 0, 128)));
        break;
    case 0x6C:
        Set(event, "filename", Str(ReadString(ds, wide ? reg_esi : reg_si, 0, 128)));
        break;
    default:
        break;
    }
}

JsonValue DpmiInput(uint16_t ax)
{
    JsonValue in = JsonValue::Object();
    switch (ax) {
    case 0x0000:
        Set(&in, "count", Num(reg_cx));
        break;
    case 0x0001: case 0x0006: case 0x000A: case 0x000B: case 0x000C:
        Set(&in, "selector", HexOut(reg_bx, 4, true));
        break;
    case 0x0002:
        Set(&in, "segment", HexOut(reg_bx, 4, true));
        break;
    case 0x0007:
        Set(&in, "selector", HexOut(reg_bx, 4, true));
        Set(&in, "base", HexOut(Pair(reg_cx, reg_dx), 8, true));
        break;
    case 0x0008:
        Set(&in, "selector", HexOut(reg_bx, 4, true));
        Set(&in, "limit", HexOut(Pair(reg_cx, reg_dx), 8, true));
        break;
    case 0x0009:
        Set(&in, "selector", HexOut(reg_bx, 4, true));
        Set(&in, "rights", HexOut(reg_cx, 4, true));
        break;
    case 0x0100:
        Set(&in, "paragraphs", HexOut(reg_bx, 4, true));
        break;
    case 0x0101:
        Set(&in, "selector", HexOut(reg_dx, 4, true));
        break;
    case 0x0200: case 0x0204: case 0x0300:
        Set(&in, "vector", HexOut(reg_bl, 2, true));
        break;
    case 0x0201:
        Set(&in, "vector", HexOut(reg_bl, 2, true));
        Set(&in, "target", Str(SegOffText(reg_cx, reg_dx, 4)));
        break;
    case 0x0205:
        Set(&in, "vector", HexOut(reg_bl, 2, true));
        Set(&in, "target", Str(SegOffText(reg_cx, cpu.code.big ? reg_edx : reg_dx, cpu.code.big ? 8 : 4)));
        break;
    case 0x0501:
        Set(&in, "size", HexOut(Pair(reg_bx, reg_cx), 8, true));
        break;
    case 0x0502:
        Set(&in, "handle", HexOut(Pair(reg_si, reg_di), 8, true));
        break;
    case 0x0503:
        Set(&in, "size", HexOut(Pair(reg_bx, reg_cx), 8, true));
        Set(&in, "handle", HexOut(Pair(reg_si, reg_di), 8, true));
        break;
    case 0x0800:
        Set(&in, "phys", HexOut(Pair(reg_bx, reg_cx), 8, true));
        Set(&in, "size", HexOut(Pair(reg_si, reg_di), 8, true));
        break;
    default:
        break;
    }
    return in;
}

JsonValue DpmiResult(uint16_t ax)
{
    JsonValue out = JsonValue::Object();
    switch (ax) {
    case 0x0000: case 0x0002: case 0x000A:
        Set(&out, "selector", HexOut(reg_ax, 4, true));
        break;
    case 0x0006:
        Set(&out, "base", HexOut(Pair(reg_cx, reg_dx), 8, true));
        break;
    case 0x0100:
        Set(&out, "segment", HexOut(reg_ax, 4, true));
        Set(&out, "selector", HexOut(reg_dx, 4, true));
        break;
    case 0x0200:
        Set(&out, "target", Str(SegOffText(reg_cx, reg_dx, 4)));
        break;
    case 0x0204:
        Set(&out, "target", Str(SegOffText(reg_cx, cpu.code.big ? reg_edx : reg_dx, cpu.code.big ? 8 : 4)));
        break;
    case 0x0501: case 0x0503:
        Set(&out, "linear", HexOut(Pair(reg_bx, reg_cx), 8, true));
        Set(&out, "handle", HexOut(Pair(reg_si, reg_di), 8, true));
        break;
    case 0x0800:
        Set(&out, "linear", HexOut(Pair(reg_bx, reg_cx), 8, true));
        break;
    default:
        break;
    }
    return out;
}

void EmitReturn(const OpenInt& open)
{
    FillFlags();
    JsonValue event = JsonValue::Object();
    Set(&event, "to_nr", Num(open.nr));
    Set(&event, "number", HexOut(open.num, 2, true));
    Set(&event, "ax_before", HexOut(open.ax, 4, true));
    Set(&event, "register", RegistersJson());
    Set(&event, "cf", Bool((reg_flags & FLAG_CF) != 0));
    if (open.num == 0x31)
        Set(&event, "result", DpmiResult(open.ax));
    EmitEvent("int_ret", event);
}

} // namespace

bool IntsOpen() { return !g_open.empty(); }

void IntsOnSoftwareInterrupt(uint8_t num, uint32_t oldeip)
{
    CheckGeneration();
    const bool pm = InPm();
    if (!pm && SegValue(cs) == CB_SEG)
        return; // DOSBox-internal call from a callback stub (spec 5.7: only the triggering INT counts)
    if (!IntWanted(num, reg_ax, pm))
        return;
    JsonValue event = JsonValue::Object();
    Set(&event, "number", HexOut(num, 2, true));
    Set(&event, "ax", HexOut(reg_ax, 4, true));
    Set(&event, "mode", Str(!cpu.pmode ? "real" : (GETFLAG(VM) ? "v86" : "pm")));
    Set(&event, "register", RegistersJson());
    AddStrings(num, &event);
    if (num == 0x31) {
        JsonValue dpmi = JsonValue::Object();
        Set(&dpmi, "function", HexOut(reg_ax, 4, true));
        for (const DpmiName& entry : kDpmi)
            if (entry.ax == reg_ax)
                Set(&dpmi, "name", Str(entry.name));
        Set(&dpmi, "input", DpmiInput(reg_ax));
        Set(&event, "dpmi", dpmi);
    }
    if (!pm) {
        for (auto it = g_open.rbegin(); it != g_open.rend(); ++it) {
            if (it->pm) {
                Set(&event, "passed_on_from", Num(it->nr));
                break;
            }
        }
    }
    EmitEvent("int", event);
    OpenInt open;
    open.nr = LastEventNr();
    open.num = num;
    open.pm = pm;
    open.cs = static_cast<uint16_t>(SegValue(cs));
    open.ret_eip = oldeip;
    open.ax = reg_ax;
    open.want_return = num == 0x31 || IntReturnsWanted();
    if (g_open.size() >= kMaxOpen)
        g_open.erase(g_open.begin());
    g_open.push_back(open);
}

void IntsOnInstruction()
{
    CheckGeneration();
    const uint16_t sel = static_cast<uint16_t>(SegValue(cs));
    const uint32_t eip = reg_eip;
    const bool pm = InPm();
    for (size_t i = g_open.size(); i-- > 0;) {
        const OpenInt& open = g_open[i];
        if (open.cs != sel || open.ret_eip != eip || open.pm != pm)
            continue;
        if (open.want_return)
            EmitReturn(open);
        g_open.resize(i);
        return;
    }
}

void IntsOnStateLoad() { g_open.clear(); } // open INTs of the replaced state never return

void IntsOnExit(uint16_t psp)
{
    QuietMem quiet;
    const uint32_t paragraphs = real_readw(static_cast<uint16_t>(psp - 1), 3); // MCB size
    std::vector<OpenInt> kept;
    for (const OpenInt& open : g_open)
        if (!open.pm && !(open.cs >= psp && open.cs < psp + paragraphs))
            kept.push_back(open);
    g_open.swap(kept);
}

} // namespace dmcp

#endif // C_DMCP
