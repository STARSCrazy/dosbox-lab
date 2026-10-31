// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "cpu.h"
#include "paging.h"
#include "regs.h"
#include "mem.h"
#include "dmcp_internal.h"
#include "dmcp_addr.h"

#include <cstring>
#include <vector>

Bitu FillFlags(void);

namespace dmcp {
namespace {

const char* const kSegNames[] = {"es", "cs", "ss", "ds", "fs", "gs"};

} // namespace

uint32_t* GeneralRegister(const std::string& name)
{
    if (name == "eax") return &reg_eax;
    if (name == "ebx") return &reg_ebx;
    if (name == "ecx") return &reg_ecx;
    if (name == "edx") return &reg_edx;
    if (name == "esi") return &reg_esi;
    if (name == "edi") return &reg_edi;
    if (name == "ebp") return &reg_ebp;
    if (name == "esp") return &reg_esp;
    if (name == "eip") return &reg_eip;
    return nullptr;
}

namespace {

std::string ModeName()
{
    if (!cpu.pmode)
        return "real";
    return GETFLAG(VM) ? "v86" : "pm";
}

JsonValue RegistersObject()
{
    FillFlags();
    JsonValue result = JsonValue::Object();

    JsonValue gp = JsonValue::Object();
    Set(&gp, "eax", Hex(reg_eax, 8));
    Set(&gp, "ebx", Hex(reg_ebx, 8));
    Set(&gp, "ecx", Hex(reg_ecx, 8));
    Set(&gp, "edx", Hex(reg_edx, 8));
    Set(&gp, "esi", Hex(reg_esi, 8));
    Set(&gp, "edi", Hex(reg_edi, 8));
    Set(&gp, "ebp", Hex(reg_ebp, 8));
    Set(&gp, "esp", Hex(reg_esp, 8));
    Set(&gp, "eip", Hex(reg_eip, 8));
    Set(&gp, "eflags", Hex(static_cast<uint32_t>(reg_flags), 8));
    Set(&result, "gp", gp);

    JsonValue seg = JsonValue::Object();
    for (int i = 0; i < 6; ++i) {
        const SegNames index = static_cast<SegNames>(i);
        JsonValue one = JsonValue::Object();
        Set(&one, "sel", Hex(SegValue(index), 4));
        Set(&one, "base", Hex(static_cast<uint32_t>(SegPhys(index)), 8));
        Set(&seg, kSegNames[i], one);
    }
    Set(&result, "seg", seg);

    JsonValue ctl = JsonValue::Object();
    Set(&ctl, "cr0", Hex(static_cast<uint32_t>(cpu.cr0), 8));
    Set(&ctl, "cr2", Hex(paging.cr2, 8));
    Set(&ctl, "cr3", Hex(static_cast<uint32_t>(PAGING_GetDirBase()), 8));
    Set(&ctl, "cr4", Hex(static_cast<uint32_t>(cpu.cr4), 8));
    Set(&result, "ctl", ctl);

    JsonValue flags = JsonValue::Object();
    Set(&flags, "cf", Bool(GETFLAG(CF) != 0));
    Set(&flags, "pf", Bool(GETFLAG(PF) != 0));
    Set(&flags, "af", Bool(GETFLAG(AF) != 0));
    Set(&flags, "zf", Bool(GETFLAG(ZF) != 0));
    Set(&flags, "sf", Bool(GETFLAG(SF) != 0));
    Set(&flags, "tf", Bool(GETFLAG(TF) != 0));
    Set(&flags, "if", Bool(GETFLAG(IF) != 0));
    Set(&flags, "df", Bool(GETFLAG(DF) != 0));
    Set(&flags, "of", Bool(GETFLAG(OF) != 0));
    Set(&result, "flags", flags);

    Set(&result, "mode", Str(ModeName()));
    Set(&result, "bits", Num(cpu.code.big ? 32 : 16));
    Set(&result, "cpl", Num(cpu.cpl));
    Set(&result, "paging", Bool((cpu.cr0 & 0x80000000u) != 0));
    return result;
}

JsonValue SegmentDescriptorJson(uint32_t index, uint16_t selector, const Descriptor& desc)
{
    JsonValue entry = JsonValue::Object();
    Set(&entry, "index", Num(index));
    Set(&entry, "selector", Hex(selector, 4));
    Set(&entry, "raw", Hex((static_cast<uint64_t>(desc.saved.fill[1]) << 32) | desc.saved.fill[0], 16));
    const Bitu type = desc.Type();
    Set(&entry, "type", Hex(type, 2));
    Set(&entry, "base", Hex(static_cast<uint32_t>(desc.GetBase()), 8));
    Set(&entry, "limit", Hex(static_cast<uint32_t>(desc.GetLimit()), 8));
    Set(&entry, "dpl", Num(desc.DPL()));
    Set(&entry, "present", Bool(desc.saved.seg.p != 0));
    Set(&entry, "bits", Num(desc.Big() ? 32 : 16));
    Set(&entry, "kind", Str((type & 0x10) == 0 ? "system" : ((type & 0x08) != 0 ? "code" : "data")));
    return entry;
}

bool ReadTable(uint32_t base, uint32_t limit, std::vector<uint8_t>* bytes, Error* error)
{
    const uint32_t size = limit + 1;
    if (size > 0x10000)
        return Fail(error, "bad_address", "descriptor table limit is implausible");
    bytes->resize(size);
    Address address;
    address.mode = "linear";
    address.has_linear = true;
    address.linear = base;
    RangeList missing;
    ReadAddress(address, bytes->data(), size, &missing);
    if (!missing.empty())
        return Fail(error, "page_not_present", "descriptor table is not completely mapped");
    return true;
}

} // namespace

void RegisterCpu()
{
    Register("cpu.registers", [](const JsonValue&, JsonValue* result, Error*) {
        *result = RegistersObject();
        return true;
    });
    Register("cpu.set_register", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string name;
        uint64_t value = 0;
        if (!ParamStr(params, "name", &name) || !ParamU64(params, "value", &value) || value > 0xFFFFFFFFull)
            return Fail(error, "bad_argument", "cpu.set_register needs name and a 32-bit value");
        if (name == "eflags") {
            FillFlags();
            reg_flags = static_cast<Bitu>(value);
        } else {
            uint32_t* reg = GeneralRegister(name);
            if (reg == nullptr)
                return Fail(error, "bad_argument", "unsupported register " + name + " (eax..edi, ebp, esp, eip, eflags)");
            *reg = static_cast<uint32_t>(value);
        }
        *result = RegistersObject();
        return true;
    });
    Register("cpu.descriptors", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string table = "gdt";
        ParamStr(params, "table", &table);
        if (!cpu.pmode)
            return Fail(error, "bad_address", "descriptor tables need protected mode");
        uint32_t base = 0, limit = 0;
        uint32_t selector_bit = 0;
        if (table == "gdt") {
            base = static_cast<uint32_t>(cpu.gdt.GetBase());
            limit = static_cast<uint32_t>(cpu.gdt.GetLimit());
        } else if (table == "ldt") {
            const uint16_t ldt_selector = static_cast<uint16_t>(cpu.gdt.SLDT());
            Descriptor ldt;
            if (!LoadDescriptor(ldt_selector, &ldt, error))
                return false;
            base = static_cast<uint32_t>(ldt.GetBase());
            limit = static_cast<uint32_t>(ldt.GetLimit());
            selector_bit = 4;
        } else if (table == "idt") {
            base = static_cast<uint32_t>(cpu.idt.GetBase());
            limit = static_cast<uint32_t>(cpu.idt.GetLimit());
        } else {
            return Fail(error, "bad_argument", "table must be gdt, ldt or idt");
        }
        std::vector<uint8_t> bytes;
        if (!ReadTable(base, limit, &bytes, error))
            return false;
        uint64_t only = 0;
        const bool filter = ParamU64(params, "selector", &only);
        JsonValue entries = JsonValue::Array();
        for (uint32_t index = 0; index * 8u + 7u < bytes.size(); ++index) {
            Descriptor desc;
            std::memcpy(&desc.saved.fill[0], &bytes[index * 8u], 4);
            std::memcpy(&desc.saved.fill[1], &bytes[index * 8u + 4u], 4);
            if (table == "idt") {
                if (filter && only != index)
                    continue;
                JsonValue gate = JsonValue::Object();
                Set(&gate, "index", Num(index));
                Set(&gate, "selector", Hex(desc.GetSelector(), 4));
                Set(&gate, "offset", Hex(static_cast<uint32_t>(desc.GetOffset()), 8));
                Set(&gate, "type", Hex(desc.Type(), 2));
                Set(&gate, "dpl", Num(desc.DPL()));
                Set(&gate, "present", Bool(desc.saved.seg.p != 0));
                entries.array.push_back(gate);
                continue;
            }
            const uint16_t selector = static_cast<uint16_t>((index << 3) | selector_bit);
            if (filter && (only & 0xFFF8u) != (selector & 0xFFF8u))
                continue;
            if (index == 0 && table == "gdt")
                continue; // null descriptor
            entries.array.push_back(SegmentDescriptorJson(index, selector, desc));
        }
        Set(result, "table", Str(table));
        Set(result, "base", Hex(base, 8));
        Set(result, "limit", Hex(limit, 4));
        Set(result, "entries", entries);
        return true;
    });
    Register("cpu.translate", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string text;
        if (!ParamStr(params, "address", &text))
            return Fail(error, "bad_argument", "parameter address is required");
        Address address;
        if (!ParseAddress(text, &address, error))
            return false;
        *result = AddressJson(address);
        if (address.has_linear && !address.has_phys)
            Set(result, "error", Str("page_not_present"));
        return true;
    });
    Register("cpu.paging", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string text;
        if (!ParamStr(params, "address", &text))
            return Fail(error, "bad_argument", "parameter address is required");
        Address address;
        if (!ParseAddress(text, &address, error))
            return false;
        if (!address.has_linear)
            return Fail(error, "bad_argument", "cpu.paging needs a linear or segmented address");
        uint64_t length = 4096;
        if (Param(params, "length") != nullptr && (!ParamU64(params, "length", &length) || length == 0))
            return Fail(error, "bad_argument", "length must be positive");
        const bool on = (cpu.cr0 & 0x80000000u) != 0;
        const uint32_t dir = static_cast<uint32_t>(PAGING_GetDirBase()) & 0xFFFFF000u;
        JsonValue pages = JsonValue::Array();
        const uint32_t first = address.linear & 0xFFFFF000u;
        const uint64_t last = static_cast<uint64_t>(address.linear) + length - 1;
        bool truncated = false;
        for (uint64_t page = first; page <= last; page += 0x1000) {
            if (pages.array.size() >= 1024) {
                truncated = true;
                break;
            }
            const uint32_t linear = static_cast<uint32_t>(page);
            JsonValue entry = JsonValue::Object();
            Set(&entry, "linear", Hex(linear, 8));
            if (!on) {
                Set(&entry, "present", Bool(true));
                Set(&entry, "phys", Hex(linear, 8));
                pages.array.push_back(entry);
                continue;
            }
            const uint32_t pde = phys_readd(static_cast<PhysPt>(dir + ((linear >> 22) << 2)));
            Set(&entry, "pde", Hex(pde, 8));
            const bool big = (pde & 0x80u) != 0 && (cpu.cr4 & 0x10u) != 0;
            Set(&entry, "uppercase", Bool(big));
            uint32_t flags_entry = pde;
            bool present = (pde & 1u) != 0;
            if (present && !big) {
                const uint32_t pte = phys_readd(static_cast<PhysPt>((pde & 0xFFFFF000u) + (((linear >> 12) & 0x3FFu) << 2)));
                Set(&entry, "pte", Hex(pte, 8));
                present = (pte & 1u) != 0;
                flags_entry = pte;
            }
            uint32_t phys = 0;
            Error ignored;
            if (present && !LinearToPhys(linear, &phys, &ignored))
                present = false; // page directory/table outside guest RAM
            Set(&entry, "present", Bool(present));
            if (present) {
                Set(&entry, "phys", Hex(phys, 8));
                Set(&entry, "rw", Bool((flags_entry & 2u) != 0));
                Set(&entry, "us", Bool((flags_entry & 4u) != 0));
            }
            pages.array.push_back(entry);
        }
        Set(result, "paging", Bool(on));
        Set(result, "cr3", Hex(static_cast<uint32_t>(PAGING_GetDirBase()), 8));
        Set(result, "pages", pages);
        Set(result, "truncated", Bool(truncated));
        return true;
    });
}

} // namespace dmcp

#endif // C_DMCP
