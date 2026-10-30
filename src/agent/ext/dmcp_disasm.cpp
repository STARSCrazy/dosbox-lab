// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "cpu.h"
#include "paging.h"
#include "regs.h"
#include "dmcp_addr.h"
#include "dmcp_disasm.h"
#include "dmcp_internal.h"
#include "dmcp_modules.h"

#include <cstdio>
#include <string>
#include <vector>

Bitu DasmI386(char* buffer, PhysPt pc, uint32_t cur_ip, bool bit32); // src/debug/debug_disasm.cpp

namespace dmcp {
namespace {

// DasmI386 reads through mem_readb_checked: an instruction crossing into a non-present page sets paging.cr2
// and cpu.exception like a guest fault would. The agent's own read must leave no trace (nor trigger watches).
Bitu QuietDasm(char* text, uint32_t linear, uint32_t offset, bool code32)
{
    QuietMem quiet;
    const uint32_t cr2 = paging.cr2;
    const Bitu which = cpu.exception.which, error = cpu.exception.error;
    const Bitu size = DasmI386(text, static_cast<PhysPt>(linear), offset, code32);
    paging.cr2 = cr2;
    cpu.exception.which = which;
    cpu.exception.error = error;
    return size;
}

bool IsPrefix(uint8_t b)
{
    return b == 0x66 || b == 0x67 || b == 0xF0 || b == 0xF2 || b == 0xF3 || b == 0x26 || b == 0x2E ||
           b == 0x36 || b == 0x3E || b == 0x64 || b == 0x65;
}

bool PeekU(uint32_t linear, int bytes, uint32_t* out)
{
    uint32_t value = 0;
    for (int i = bytes - 1; i >= 0; --i) {
        uint8_t b = 0;
        if (!PeekLinear(linear + static_cast<uint32_t>(i), &b))
            return false;
        value = (value << 8) | b;
    }
    *out = value;
    return true;
}

struct Target {
    bool far_ = false;
    uint16_t seg = 0;
    uint32_t off = 0;
};

// Direct branch target of the instruction (near: new offset in the same segment; far: seg:off).
bool DirectTarget(uint32_t linear, uint32_t offset, bool code32, uint32_t length, Target* t)
{
    bool op32 = code32;
    uint32_t i = 0;
    uint8_t b = 0;
    for (; i < 15; ++i) {
        if (!PeekLinear(linear + i, &b))
            return false;
        if (!IsPrefix(b))
            break;
        if (b == 0x66)
            op32 = !code32;
    }
    const uint32_t mask = op32 ? 0xFFFFFFFFu : 0xFFFFu;
    const uint32_t next = offset + length;
    uint32_t raw = 0;
    if (b == 0xEB || (b >= 0x70 && b <= 0x7F) || (b >= 0xE0 && b <= 0xE3)) {
        if (!PeekU(linear + i + 1, 1, &raw))
            return false;
        t->off = (next + static_cast<uint32_t>(static_cast<int32_t>(static_cast<int8_t>(raw)))) & mask;
        return true;
    }
    if (b == 0xE8 || b == 0xE9) {
        if (!PeekU(linear + i + 1, op32 ? 4 : 2, &raw))
            return false;
        const uint32_t rel = op32 ? raw : static_cast<uint32_t>(static_cast<int32_t>(static_cast<int16_t>(raw)));
        t->off = (next + rel) & mask;
        return true;
    }
    if (b == 0x0F) {
        uint8_t b2 = 0;
        if (!PeekLinear(linear + i + 1, &b2) || b2 < 0x80 || b2 > 0x8F)
            return false;
        if (!PeekU(linear + i + 2, op32 ? 4 : 2, &raw))
            return false;
        const uint32_t rel = op32 ? raw : static_cast<uint32_t>(static_cast<int32_t>(static_cast<int16_t>(raw)));
        t->off = (next + rel) & mask;
        return true;
    }
    if (b == 0x9A || b == 0xEA) {
        uint32_t seg = 0;
        if (!PeekU(linear + i + 1, op32 ? 4 : 2, &raw) || !PeekU(linear + i + 1 + (op32 ? 4 : 2), 2, &seg))
            return false;
        t->far_ = true;
        t->off = raw;
        t->seg = static_cast<uint16_t>(seg);
        return true;
    }
    return false;
}

std::string AddressText(const Address& a)
{
    char text[40];
    if (!a.has_selector)
        std::snprintf(text, sizeof(text), "l:%08X", a.linear);
    else if (a.modus == "real")
        std::snprintf(text, sizeof(text), "r:%04X:%04X", a.selector, a.offset);
    else
        std::snprintf(text, sizeof(text), "%04X:%08X", a.selector, a.offset);
    return text;
}

JsonValue Annotated(const Address& a, bool file_format)
{
    JsonValue object = AddressJson(a, file_format);
    AnnotateAddress(a, &object, file_format);
    return object;
}

bool TargetAddress(const Address& cur, const Target& t, Address* out)
{
    Error ignored;
    if (t.far_) {
        char text[40];
        if (cur.modus == "real")
            std::snprintf(text, sizeof(text), "r:%04X:%04X", t.seg, t.off & 0xFFFFu);
        else
            std::snprintf(text, sizeof(text), "%04X:%08X", t.seg, t.off);
        return ParseAddress(text, out, &ignored);
    }
    Address a = cur;
    if (cur.has_selector) {
        a.offset = t.off;
        a.linear = cur.base + t.off;
    } else {
        a.linear = t.off;
    }
    a.has_phys = false;
    ResolvePhys(&a);
    *out = a;
    return true;
}

JsonValue Line(const Address& cur, int bits, bool file_format, uint32_t* length)
{
    const uint32_t offset = cur.has_selector ? cur.offset : cur.linear;
    char text[512] = {0};
    Bitu size = QuietDasm(text, cur.linear, offset, bits == 32);
    if (size == 0 || size > 15)
        size = 1;
    std::string line(text);
    while (!line.empty() && (line.back() == ' ' || line.back() == '\t'))
        line.pop_back();
    std::string bytes;
    for (Bitu i = 0; i < size; ++i) {
        uint8_t b = 0;
        char hex[4];
        PeekLinear(cur.linear + static_cast<uint32_t>(i), &b);
        std::snprintf(hex, sizeof(hex), "%02X", b);
        bytes += hex;
    }
    JsonValue entry = JsonValue::Object();
    Set(&entry, "adr", Annotated(cur, file_format));
    Set(&entry, "bytes", Str(bytes));
    Set(&entry, "laenge", Num(size));
    Set(&entry, "text", Str(line));
    Target t;
    Address target;
    if (DirectTarget(cur.linear, offset, bits == 32, static_cast<uint32_t>(size), &t) &&
        TargetAddress(cur, t, &target))
        Set(&entry, "ziel", Annotated(target, file_format));
    *length = static_cast<uint32_t>(size);
    return entry;
}

} // namespace

uint32_t InstructionLength(uint32_t linear, uint32_t offset, bool code32)
{
    char text[512] = {0};
    const Bitu size = QuietDasm(text, linear, offset, code32);
    return (size == 0 || size > 15) ? 1u : static_cast<uint32_t>(size);
}

bool IsStepOverInstruction(uint32_t linear)
{
    bool rep = false;
    uint32_t i = 0;
    uint8_t b = 0;
    for (; i < 15; ++i) {
        if (!PeekLinear(linear + i, &b))
            return false;
        if (!IsPrefix(b))
            break;
        if (b == 0xF2 || b == 0xF3)
            rep = true;
    }
    if (b == 0xE8 || b == 0x9A || b == 0xCD || b == 0xCC || b == 0xCE)
        return true;
    if (b == 0xFF) {
        uint8_t modrm = 0;
        if (!PeekLinear(linear + i + 1, &modrm))
            return false;
        const uint8_t reg = (modrm >> 3) & 7;
        return reg == 2 || reg == 3;
    }
    const bool string_op = (b >= 0xA4 && b <= 0xA7) || (b >= 0xAA && b <= 0xAF) || (b >= 0x6C && b <= 0x6F);
    return rep && string_op;
}

void RegisterDisasm()
{
    Register("cpu.disassemble", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string text, name;
        Address cur;
        if (Param(params, "adresse") != nullptr) {
            if (!ParamStr(params, "adresse", &text))
                return Fail(error, "bad_argument", "adresse must be a string");
            if (!ParseAddress(text, &cur, error))
                return false;
        } else {
            cur = SegCacheAddress(cs, reg_eip);
        }
        if (!cur.has_linear)
            return Fail(error, "bad_argument", "disassembly needs a linear address (p: is not supported)");
        if (Param(params, "name") != nullptr && !ParamStr(params, "name", &name))
            return Fail(error, "bad_argument", "name must be a string");
        uint64_t count = 10;
        if (Param(params, "anzahl") != nullptr && !ParamU64(params, "anzahl", &count))
            return Fail(error, "bad_argument", "anzahl must be a number");
        const uint64_t max = name.empty() ? 200 : 100000;
        if (count == 0 || count > max)
            return Fail(error, "bad_argument", "anzahl must be between 1 and " + std::to_string(max) +
                                                   (name.empty() ? " (more with name: written to a file)" : ""));
        uint64_t bits = cur.bits != 0 ? static_cast<uint64_t>(cur.bits) : (cpu.code.big ? 32u : 16u);
        if (Param(params, "bits") != nullptr && (!ParamU64(params, "bits", &bits) || (bits != 16 && bits != 32)))
            return Fail(error, "bad_argument", "bits must be 16 or 32");
        const bool to_file = !name.empty();
        std::vector<JsonValue> lines;
        for (uint64_t i = 0; i < count; ++i) {
            uint8_t probe = 0;
            if (!PeekLinear(cur.linear, &probe)) {
                Set(result, "ende", Str("nicht lesbar"));
                break;
            }
            uint32_t length = 1;
            lines.push_back(Line(cur, static_cast<int>(bits), to_file, &length));
            cur = Offset(cur, length);
        }
        Set(result, "naechste", Str(AddressText(cur)));
        if (!to_file) {
            JsonValue list = JsonValue::Array();
            list.array = lines;
            Set(result, "zeilen", list);
            return true;
        }
        std::string body;
        for (const JsonValue& line : lines)
            body += dosbox_agent::AGENT_SerializeJson(line) + "\n";
        std::string path;
        if (!WriteOutputFile(name + ".jsonl", body.data(), body.size(), &path, error))
            return false;
        Set(result, "pfad", Str(path));
        Set(result, "anzahl", Num(lines.size()));
        return true;
    });
}

} // namespace dmcp

#endif // C_DMCP
