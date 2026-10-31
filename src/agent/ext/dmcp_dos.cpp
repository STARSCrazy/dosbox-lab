// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "mem.h"
#include "dos_inc.h"
#include "dmcp_addr.h"
#include "dmcp_internal.h"
#include "dmcp_modules.h"

#include <cstdio>
#include <set>
#include <string>

namespace dmcp {
namespace {

std::string Name8(uint32_t linear)
{
    std::string name;
    for (uint32_t i = 0; i < 8; ++i) {
        const uint8_t c = phys_readb(static_cast<PhysPt>(linear + i));
        if (c == 0)
            break;
        name.push_back(c >= 0x20 && c < 0x7F ? static_cast<char>(c) : '?');
    }
    return name;
}

std::string PrintableName(const char* name)
{
    std::string out;
    for (size_t i = 0; name != nullptr && name[i] != 0 && i < 260; ++i) {
        const unsigned char c = static_cast<unsigned char>(name[i]);
        out.push_back(c >= 0x20 && c < 0x7F ? static_cast<char>(c) : '?');
    }
    return out;
}

std::string Tail(uint16_t psp)
{
    const uint32_t base = static_cast<uint32_t>(psp) << 4;
    const uint8_t len = phys_readb(static_cast<PhysPt>(base + 0x80));
    std::string tail;
    for (uint8_t i = 0; i < len && i < 127; ++i) {
        const uint8_t c = phys_readb(static_cast<PhysPt>(base + 0x81 + i));
        tail.push_back(c >= 0x20 && c < 0x7F ? static_cast<char>(c) : '?');
    }
    return tail;
}

std::string SegOffText(uint32_t realpt)
{
    char text[16];
    snprintf(text, sizeof(text), "%04X:%04X", static_cast<unsigned>(realpt >> 16),
                      static_cast<unsigned>(realpt & 0xFFFFu));
    return text;
}

} // namespace

void RegisterDos()
{
    Register("dos.mcb", [](const JsonValue&, JsonValue* result, Error*) {
        JsonValue list = JsonValue::Array();
        uint16_t seg = dos.firstMCB;
        std::string end = "aborted";
        for (int n = 0; n < 4096; ++n) {
            const uint32_t lin = static_cast<uint32_t>(seg) << 4;
            if (!PhysInRam(lin, 16)) {
                end = "outside";
                break;
            }
            const uint8_t type = phys_readb(static_cast<PhysPt>(lin));
            if (type != 'M' && type != 'Z') {
                end = "broken";
                break;
            }
            const uint16_t owner = phys_readw(static_cast<PhysPt>(lin + 1));
            const uint16_t size = phys_readw(static_cast<PhysPt>(lin + 3));
            JsonValue block = JsonValue::Object();
            Set(&block, "seg", Hex(seg, 4));
            Set(&block, "type", Str(std::string(1, static_cast<char>(type))));
            Set(&block, "owner", Hex(owner, 4));
            Set(&block, "paragraphs", Num(size));
            Set(&block, "size", Num(static_cast<uint64_t>(size) * 16));
            Set(&block, "name", Str(Name8(lin + 8)));
            Set(&block, "free", Bool(owner == 0));
            Set(&block, "program", Bool(owner == static_cast<uint16_t>(seg + 1)));
            list.array.push_back(block);
            if (type == 'Z') {
                end = "Z";
                break;
            }
            seg = static_cast<uint16_t>(seg + size + 1);
        }
        Set(result, "blocks", list);
        Set(result, "end", Str(end));
        return true;
    });
    Register("dos.programs", [](const JsonValue&, JsonValue* result, Error*) {
        JsonValue list = JsonValue::Array();
        std::set<uint16_t> seen;
        uint16_t psp = dos.psp();
        bool first = true;
        while (psp != 0 && seen.count(psp) == 0 && seen.size() < 64 &&
               PhysInRam(static_cast<uint32_t>(psp) << 4, 0x100)) {
            seen.insert(psp);
            const uint32_t base = static_cast<uint32_t>(psp) << 4;
            const uint16_t parent = phys_readw(static_cast<PhysPt>(base + 0x16));
            JsonValue entry = JsonValue::Object();
            Set(&entry, "psp", Hex(psp, 4));
            Set(&entry, "parent", Hex(parent, 4));
            Set(&entry, "environment", Hex(phys_readw(static_cast<PhysPt>(base + 0x2C)), 4));
            const uint32_t mcb = base - 16;
            const bool own_mcb = phys_readw(static_cast<PhysPt>(mcb + 1)) == psp;
            Set(&entry, "name", Str(own_mcb ? Name8(mcb + 8) : std::string()));
            Set(&entry, "command_line", Str(Tail(psp)));
            Set(&entry, "current", Bool(first));
            for (const Module& m : Modules()) {
                if (m.has_load && m.psp == psp && m.kind != "overlay") {
                    Set(&entry, "module", Str(m.name));
                    Set(&entry, "file", Str(m.file));
                    Set(&entry, "load_seg", Hex(m.load_seg, 4));
                    Set(&entry, "rel_base", Hex(m.rel_base, 4));
                }
            }
            list.array.push_back(entry);
            first = false;
            if (parent == psp)
                break; // the shell is its own parent
            psp = parent;
        }
        Set(result, "programs", list);
        return true;
    });
    Register("dos.files", [](const JsonValue&, JsonValue* result, Error*) {
        JsonValue files = JsonValue::Array();
        for (uint16_t i = 0; i < DOS_FILES; ++i) {
            if (Files[i] == nullptr)
                continue;
            JsonValue entry = JsonValue::Object();
            Set(&entry, "sft", Num(i));
            Set(&entry, "name", Str(PrintableName(Files[i]->GetName())));
            Set(&entry, "refs", Num(static_cast<uint64_t>(Files[i]->refCtr)));
            files.array.push_back(entry);
        }
        JsonValue handles = JsonValue::Array();
        const uint32_t psp_lin = static_cast<uint32_t>(dos.psp()) << 4;
        if (!PhysInRam(psp_lin, 0x100)) {
            Set(result, "jft_error", Str("PSP not in RAM"));
        } else {
            const uint16_t count = phys_readw(static_cast<PhysPt>(psp_lin + 0x32));
            const uint32_t ptr = phys_readd(static_cast<PhysPt>(psp_lin + 0x34));
            const uint32_t jft = ((ptr >> 16) << 4) + (ptr & 0xFFFFu);
            if (!PhysInRam(jft, count == 0 ? 1 : count)) {
                Set(result, "jft_error", Str("job file table outside RAM"));
            } else {
                const uint16_t n = count > 255 ? 255 : count;
                if (count > 255)
                    Set(result, "truncated", Bool(true));
                for (uint16_t h = 0; h < n; ++h) {
                    const uint8_t sft = phys_readb(static_cast<PhysPt>(jft + h));
                    if (sft == 0xFF)
                        continue;
                    JsonValue entry = JsonValue::Object();
                    Set(&entry, "handle", Num(h));
                    Set(&entry, "sft", Num(sft));
                    handles.array.push_back(entry);
                }
            }
        }
        Set(result, "files", files);
        Set(result, "handles", handles);
        return true;
    });
    Register("dos.target", [](const JsonValue&, JsonValue* result, Error*) {
        const Module* m = TargetModule();
        Set(result, "found", Bool(m != nullptr));
        if (m == nullptr)
            return true;
        Set(result, "module", Str(m->name));
        Set(result, "file", Str(m->file));
        Set(result, "kind", Str(m->kind));
        Set(result, "psp", Hex(m->psp, 4));
        Set(result, "load_seg", Hex(m->load_seg, 4));
        Set(result, "rel_base", Hex(m->rel_base, 4));
        Set(result, "relocation", Hex(m->kind == "com" ? 0 : m->load_seg, 4));
        Set(result, "paragraphs", Num(m->paragraphs));
        if (m->has_entry) {
            Set(result, "entry", Str(SegOffText(m->csip)));
            Set(result, "stack", Str(SegOffText(m->sssp)));
        }
        Set(result, "command_line", Str(m->command_line));
        Set(result, "exec_count", Num(m->exec_count));
        return true;
    });
}

} // namespace dmcp

#endif // C_DMCP
