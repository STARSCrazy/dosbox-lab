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
        std::string ende = "abgebrochen";
        for (int n = 0; n < 4096; ++n) {
            const uint32_t lin = static_cast<uint32_t>(seg) << 4;
            if (!PhysInRam(lin, 16)) {
                ende = "ausserhalb";
                break;
            }
            const uint8_t typ = phys_readb(static_cast<PhysPt>(lin));
            if (typ != 'M' && typ != 'Z') {
                ende = "kaputt";
                break;
            }
            const uint16_t owner = phys_readw(static_cast<PhysPt>(lin + 1));
            const uint16_t size = phys_readw(static_cast<PhysPt>(lin + 3));
            JsonValue block = JsonValue::Object();
            Set(&block, "seg", Hex(seg, 4));
            Set(&block, "typ", Str(std::string(1, static_cast<char>(typ))));
            Set(&block, "besitzer", Hex(owner, 4));
            Set(&block, "paragraphen", Num(size));
            Set(&block, "groesse", Num(static_cast<uint64_t>(size) * 16));
            Set(&block, "name", Str(Name8(lin + 8)));
            Set(&block, "frei", Bool(owner == 0));
            Set(&block, "programm", Bool(owner == static_cast<uint16_t>(seg + 1)));
            list.array.push_back(block);
            if (typ == 'Z') {
                ende = "Z";
                break;
            }
            seg = static_cast<uint16_t>(seg + size + 1);
        }
        Set(result, "bloecke", list);
        Set(result, "ende", Str(ende));
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
            Set(&entry, "eltern", Hex(parent, 4));
            Set(&entry, "umgebung", Hex(phys_readw(static_cast<PhysPt>(base + 0x2C)), 4));
            const uint32_t mcb = base - 16;
            const bool own_mcb = phys_readw(static_cast<PhysPt>(mcb + 1)) == psp;
            Set(&entry, "name", Str(own_mcb ? Name8(mcb + 8) : std::string()));
            Set(&entry, "befehlszeile", Str(Tail(psp)));
            Set(&entry, "aktuell", Bool(first));
            for (const Module& m : Modules()) {
                if (m.has_load && m.psp == psp && m.art != "overlay") {
                    Set(&entry, "modul", Str(m.name));
                    Set(&entry, "datei", Str(m.datei));
                    Set(&entry, "load_seg", Hex(m.load_seg, 4));
                    Set(&entry, "rel_basis", Hex(m.rel_basis, 4));
                }
            }
            list.array.push_back(entry);
            first = false;
            if (parent == psp)
                break; // the shell is its own parent
            psp = parent;
        }
        Set(result, "programme", list);
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
            Set(&entry, "referenzen", Num(static_cast<uint64_t>(Files[i]->refCtr)));
            files.array.push_back(entry);
        }
        JsonValue handles = JsonValue::Array();
        const uint32_t psp_lin = static_cast<uint32_t>(dos.psp()) << 4;
        if (!PhysInRam(psp_lin, 0x100)) {
            Set(result, "jft_fehler", Str("PSP not in RAM"));
        } else {
            const uint16_t count = phys_readw(static_cast<PhysPt>(psp_lin + 0x32));
            const uint32_t ptr = phys_readd(static_cast<PhysPt>(psp_lin + 0x34));
            const uint32_t jft = ((ptr >> 16) << 4) + (ptr & 0xFFFFu);
            if (!PhysInRam(jft, count == 0 ? 1 : count)) {
                Set(result, "jft_fehler", Str("job file table outside RAM"));
            } else {
                const uint16_t n = count > 255 ? 255 : count;
                if (count > 255)
                    Set(result, "abgeschnitten", Bool(true));
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
        Set(result, "dateien", files);
        Set(result, "handles", handles);
        return true;
    });
    Register("dos.target", [](const JsonValue&, JsonValue* result, Error*) {
        const Module* m = TargetModule();
        Set(result, "gefunden", Bool(m != nullptr));
        if (m == nullptr)
            return true;
        Set(result, "modul", Str(m->name));
        Set(result, "datei", Str(m->datei));
        Set(result, "art", Str(m->art));
        Set(result, "psp", Hex(m->psp, 4));
        Set(result, "load_seg", Hex(m->load_seg, 4));
        Set(result, "rel_basis", Hex(m->rel_basis, 4));
        Set(result, "relocation", Hex(m->art == "com" ? 0 : m->load_seg, 4));
        Set(result, "paragraphen", Num(m->paragraphs));
        if (m->has_entry) {
            Set(result, "einsprung", Str(SegOffText(m->csip)));
            Set(result, "stack", Str(SegOffText(m->sssp)));
        }
        Set(result, "befehlszeile", Str(m->befehlszeile));
        Set(result, "exec_anzahl", Num(m->exec_count));
        return true;
    });
}

} // namespace dmcp

#endif // C_DMCP
