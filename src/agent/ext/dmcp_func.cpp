// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "agent/dmcp.h"
#include "callback.h"
#include "control.h"
#include "cpu.h"
#include "dos_inc.h"
#include "mem.h"
#include "paging.h"
#include "regs.h"
#include "dmcp_addr.h"
#include "dmcp_record.h"
#include "dmcp_state.h"

#include <algorithm>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <vector>

bool CPU_IsHLTed(void); // cpu.cpp: guest executed HLT and waits for an interrupt

Bitu FillFlags(void);

bool DMCP_irq_hold = false; // pic.cpp: hardware IRQs wait while a func run holds them

namespace dmcp {
namespace {

// One recorded call, run again from its savestate up to the return.
struct Run {
    bool active = false;   // running; the result is measured at the next stop (StopAt)
    bool finished = false; // result is ready
    std::string state, name = "func", kind;
    uint64_t call_id = 0;
    uint16_t psp = 0; // DOS PSP at the start: only this program's end ends the run
    uint16_t ret_sel = 0, ss = 0;
    uint32_t ret_off = 0, sp_after = 0, esp_in = 0;
    uint64_t max_insn = 10000000, insn = 0;
    bool irqs = false;
    std::string end; // set by FuncShouldStop / FuncOnExit before the stop
    uint32_t min_sp = 0;
    uint32_t ss_base = 0;
    // Other stacks the run uses (INT with stack switch, extender stacks): one area per SS, linear [base+min, base+top).
    struct StackArea {
        uint16_t ss;
        uint32_t base, min_sp, top;
    };
    std::vector<StackArea> areas;
    // The stack seen at the previous instruction (one visit lasts until SS or its base changes).
    uint16_t cur_ss = 0;
    uint32_t cur_base = 0, cur_min = 0, cur_last = 0, cur_top = 0;
    bool cur_sampled = false, cur_tss_top = false, cur_skip = false;
    Bitu prev_cpl = 0;
    std::vector<uint8_t> before; // guest RAM at the start (physical)
    std::string stop_reason_override; // state.load ends the run without a stop
    JsonValue regs_in, flags_in, written;
    // Software INTs of the run; an open one gets regs_out at its return point.
    JsonValue ints = JsonValue::Array();
    uint64_t ints_dropped = 0;
    struct OpenInt {
        uint16_t cs;
        uint32_t ret_eip;
        bool pm;
        size_t index;
    };
    std::vector<OpenInt> open_ints;
    JsonValue result;
};
Run g;

uint32_t CurrentSp() { return reg_esp & cpu.stack.mask; }

// Same register set as calls.jsonl.
JsonValue RegsJson()
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
    return r;
}

JsonValue FlagsJson()
{
    FillFlags();
    return HexOut(static_cast<uint32_t>(reg_flags), 8, true);
}

const char* EndFromArt(const std::string& kind)
{
    return kind == "far" ? "retf" : (kind == "int" ? "iret" : "ret");
}

bool ReadCall(const JsonValue& sidecar, Run* run, Error* error)
{
    const JsonValue* a = sidecar.type == JsonType::Object ? Param(sidecar, "call") : nullptr;
    if (a == nullptr || a->type != JsonType::Object)
        return Fail(error, "bad_argument", "state has no call (only states written by calls states work)");
    const JsonValue* ret = Param(*a, "return_address");
    uint32_t sel = 0, off = 0, ss_value = 0, sp_after = 0, esp_in = 0;
    uint64_t call_id = 0;
    if (ret == nullptr || ret->type != JsonType::Object || !HexField(*ret, "sel", &sel) || !HexField(*ret, "off", &off) ||
        !HexField(*a, "ss", &ss_value) || !HexField(*a, "esp_after", &sp_after) || !HexField(*a, "esp_in", &esp_in) ||
        !ParamStr(*a, "kind", &run->kind) || !ParamU64(*a, "call_id", &call_id))
        return Fail(error, "bad_argument", "call in the sidecar is incomplete");
    run->ret_sel = static_cast<uint16_t>(sel);
    run->ret_off = off;
    run->ss = static_cast<uint16_t>(ss_value);
    run->sp_after = sp_after;
    run->esp_in = esp_in;
    run->call_id = call_id;
    return true;
}

// regs {name: value}, then stack bytes at SS:ESP, then write [{address, hex}]. apply=false only checks the
// form (before the state is loaded); addresses are translated with apply=true, in the loaded state.
bool ApplyInputs(const JsonValue& params, bool apply, Error* error)
{
    if (const JsonValue* regs = Param(params, "regs")) {
        if (regs->type != JsonType::Object)
            return Fail(error, "bad_argument", "regs must be an object {name: value}");
        for (const auto& item : regs->object) {
            uint32_t value = 0;
            if (!HexField(*regs, item.first, &value))
                return Fail(error, "bad_argument", "regs." + item.first + " must be a hex string or number");
            if (item.first == "eflags") {
                if (apply) {
                    FillFlags();
                    reg_flags = static_cast<Bitu>(value);
                }
                continue;
            }
            uint32_t* reg = GeneralRegister(item.first);
            if (reg == nullptr)
                return Fail(error, "bad_argument", "unsupported register " + item.first + " (eax..edi, ebp, esp, eip, eflags)");
            if (apply)
                *reg = value;
        }
    }
    if (Param(params, "stack") != nullptr) {
        std::string hex;
        std::vector<uint8_t> data;
        if (!ParamStr(params, "stack", &hex) || !FromHex(hex, &data) || data.size() > 65536)
            return Fail(error, "bad_argument", "stack must be a non-empty even-length hex string of at most 64 KiB");
        if (apply && !WriteAddress(SegCacheAddress(ss, CurrentSp()), data.data(), data.size(), error))
            return false;
    }
    if (apply)
        g.written = JsonValue::Array();
    if (const JsonValue* list = Param(params, "write")) {
        if (list->type != JsonType::Array)
            return Fail(error, "bad_argument", "write must be a list of {address, hex}");
        for (size_t i = 0; i < list->array.size(); ++i) {
            const JsonValue& item = list->array[i];
            const std::string where = "write[" + std::to_string(i) + "]: ";
            std::string text, hex;
            std::vector<uint8_t> data;
            Address address;
            if (item.type != JsonType::Object || !ParamStr(item, "address", &text) || !ParamStr(item, "hex", &hex) ||
                !FromHex(hex, &data) || data.size() > (16u << 20))
                return Fail(error, "bad_argument", where + "needs address and a non-empty even-length hex string");
            if (!apply)
                continue;
            Error inner;
            if (!ParseAddress(text, &address, &inner) || !WriteAddress(address, data.data(), data.size(), &inner))
                return Fail(error, "bad_argument", where + inner.message);
            JsonValue o = JsonValue::Object();
            Set(&o, "address", AddressJson(address, true));
            Set(&o, "length", Num(data.size()));
            g.written.array.push_back(o);
        }
    }
    return true;
}

// Physical page -> all linear pages mapping to it (only pages in `wanted`), sorted. Without paging: identity.
std::map<uint32_t, std::vector<uint32_t>> ReverseMap(const std::set<uint32_t>& wanted)
{
    std::map<uint32_t, std::vector<uint32_t>> out;
    if ((cpu.cr0 & 0x80000000u) == 0) {
        for (uint32_t p : wanted)
            out[p].push_back(p);
        return out;
    }
    const uint32_t dir = static_cast<uint32_t>(PAGING_GetDirBase()) & 0xFFFFF000u;
    const bool pse = (cpu.cr4 & 0x10u) != 0;
    if (!PhysInRam(dir, 0x1000))
        return out;
    for (uint32_t i = 0; i < 1024; ++i) {
        const uint32_t pde = phys_readd(static_cast<PhysPt>(dir + i * 4u));
        if ((pde & 1u) == 0)
            continue;
        if (pse && (pde & 0x80u) != 0) { // 4 MB page
            for (uint32_t j = 0; j < 1024; ++j) {
                const uint32_t phys = (pde & 0xFFC00000u) + (j << 12);
                if (wanted.count(phys) != 0)
                    out[phys].push_back((i << 22) | (j << 12));
            }
            continue;
        }
        const uint32_t table = pde & 0xFFFFF000u;
        if (!PhysInRam(table, 0x1000))
            continue;
        for (uint32_t j = 0; j < 1024; ++j) {
            const uint32_t pte = phys_readd(static_cast<PhysPt>(table + j * 4u));
            if ((pte & 1u) == 0)
                continue;
            const uint32_t phys = pte & 0xFFFFF000u;
            if (wanted.count(phys) != 0)
                out[phys].push_back((i << 22) | (j << 12));
        }
    }
    for (auto& e : out)
        std::sort(e.second.begin(), e.second.end());
    return out;
}

// Stack top after a switch to an inner ring: SSn:ESPn of the current TSS (n = new CPL), if SS matches.
bool TssStackTop(uint32_t* top)
{
    Descriptor desc;
    if (!cpu.gdt.GetDescriptor(CPU_STR(), desc))
        return false;
    const Bitu type = desc.Type();
    const bool is386 = type == DESC_386_TSS_A || type == DESC_386_TSS_B;
    if (!is386 && type != DESC_286_TSS_A && type != DESC_286_TSS_B)
        return false;
    const uint32_t level = static_cast<uint32_t>(cpu.cpl);
    const uint32_t at = static_cast<uint32_t>(desc.GetBase()) + (is386 ? 4u + 8u * level : 2u + 4u * level);
    uint32_t phys_esp = 0, phys_ss = 0;
    Error ignored;
    if (!LinearToPhys(at, &phys_esp, &ignored) || !LinearToPhys(at + (is386 ? 4u : 2u), &phys_ss, &ignored) ||
        !PhysInRam(phys_esp, is386 ? 4 : 2) || !PhysInRam(phys_ss, 2))
        return false;
    const uint32_t esp = is386 ? phys_readd(static_cast<PhysPt>(phys_esp)) : phys_readw(static_cast<PhysPt>(phys_esp));
    if ((phys_readw(static_cast<PhysPt>(phys_ss)) & 0xFFFCu) != (SegValue(ss) & 0xFFFCu))
        return false;
    *top = esp & cpu.stack.mask;
    return true;
}

// Ends the visit of a foreign stack: its used part joins the area of that stack.
void CloseStackVisit()
{
    if (g.cur_ss == g.ss || !g.cur_sampled)
        return;
    const uint32_t top = g.cur_tss_top ? std::max(g.cur_top, g.cur_last) : g.cur_last;
    if (g.cur_min >= top)
        return;
    for (Run::StackArea& a : g.areas) {
        if (a.ss == g.cur_ss && a.base == g.cur_base) {
            a.min_sp = std::min(a.min_sp, g.cur_min);
            a.top = std::max(a.top, top);
            return;
        }
    }
    if (g.areas.size() < 64)
        g.areas.push_back({g.cur_ss, g.cur_base, g.cur_min, top});
}

// Tracks the stacks per instruction. A switch to an inner ring (INT/IRQ through a gate) starts at the TSS stack
// top, so the frame the CPU pushed counts as stack. Any other switch (mov ss, lss) leaves ESP possibly from the
// old stack for one instruction, so that sample is skipped; a foreign stack then ends at its last ESP.
void TrackStack()
{
    const uint16_t sel = static_cast<uint16_t>(SegValue(ss));
    const uint32_t base = static_cast<uint32_t>(SegPhys(ss));
    if (sel != g.cur_ss || base != g.cur_base) {
        CloseStackVisit();
        g.cur_ss = sel;
        g.cur_base = base;
        g.cur_sampled = false;
        g.cur_tss_top = cpu.pmode && cpu.cpl < g.prev_cpl && TssStackTop(&g.cur_top);
        g.cur_skip = cpu.cpl == g.prev_cpl;
    }
    g.prev_cpl = cpu.cpl;
    if (g.cur_skip) {
        g.cur_skip = false;
        return;
    }
    const uint32_t sp = CurrentSp();
    if (sel == g.ss) {
        g.min_sp = std::min(g.min_sp, sp);
        return;
    }
    g.cur_min = g.cur_sampled ? std::min(g.cur_min, sp) : sp;
    g.cur_last = sp;
    g.cur_sampled = true;
}

void AddLinearRange(uint32_t start, uint32_t stop, std::vector<std::pair<uint32_t, uint32_t>>* out)
{
    if (start >= stop || stop - start > (16u << 20))
        return;
    for (uint32_t lin = start; lin < stop;) {
        const uint32_t end = std::min((lin | 0xFFFu) + 1u, stop);
        uint32_t phys = 0;
        Error ignored;
        if (LinearToPhys(lin, &phys, &ignored))
            out->push_back({phys, phys + (end - lin)});
        lin = end;
    }
}

// Physical bytes of the stacks: the function's own, linear [ss_base + lowest sp, ss_base + esp_in), and the used
// part of every other stack of the run.
std::vector<std::pair<uint32_t, uint32_t>> StackExclusion() // physical [start, end)
{
    std::vector<std::pair<uint32_t, uint32_t>> out;
    AddLinearRange(g.ss_base + g.min_sp, g.ss_base + g.esp_in, &out);
    for (const Run::StackArea& a : g.areas)
        AddLinearRange(a.base + a.min_sp, a.base + a.top, &out);
    return out;
}

std::string HexUpper(const uint8_t* data, size_t len)
{
    static const char digits[] = "0123456789ABCDEF";
    std::string s;
    s.reserve(len * 2);
    for (size_t i = 0; i < len; ++i) {
        s.push_back(digits[data[i] >> 4]);
        s.push_back(digits[data[i] & 15]);
    }
    return s;
}

// Changed guest RAM since func.start, without the function's own stack; gaps up to 16 bytes are bridged.
JsonValue ChangedJson(bool* cut)
{
    *cut = false;
    JsonValue list = JsonValue::Array();
    const size_t ram = std::min(g.before.size(), static_cast<size_t>(GuestRamBytes()));
    const auto skip = StackExclusion();
    auto excluded = [&skip](uint32_t phys) {
        for (const auto& r : skip)
            if (phys >= r.first && phys < r.second)
                return true;
        return false;
    };
    // 1. Runs of changed physical bytes inside one 4 KB page.
    std::vector<std::pair<uint32_t, uint32_t>> runs; // [start, end)
    for (size_t page = 0; page < ram; page += 4096) {
        const size_t len = std::min<size_t>(4096, ram - page);
        if (std::memcmp(&g.before[page], MemBase + page, len) == 0)
            continue;
        for (size_t i = page; i < page + len; ++i) {
            if (g.before[i] == MemBase[i] || excluded(static_cast<uint32_t>(i)))
                continue;
            if (!runs.empty() && runs.back().first / 4096 == i / 4096 && i - runs.back().second <= 16)
                runs.back().second = static_cast<uint32_t>(i + 1);
            else
                runs.push_back({static_cast<uint32_t>(i), static_cast<uint32_t>(i + 1)});
        }
    }
    // 2. Linear addresses of the changed pages.
    std::set<uint32_t> pages;
    for (const auto& r : runs)
        pages.insert(r.first & 0xFFFFF000u);
    const auto rev = ReverseMap(pages);
    // 3. Entries; runs across a page boundary merge when both phys and linear continue.
    struct Entry {
        uint32_t phys, len;
        bool has_lin;
        uint32_t lin;
        std::vector<uint32_t> more;
    };
    std::vector<Entry> entries;
    for (const auto& r : runs) {
        Entry e{r.first, r.second - r.first, false, 0, {}};
        const auto it = rev.find(r.first & 0xFFFFF000u);
        if (it != rev.end() && !it->second.empty()) {
            e.has_lin = true;
            e.lin = it->second[0] + (r.first & 0xFFFu);
            for (size_t k = 1; k < it->second.size(); ++k)
                e.more.push_back(it->second[k] + (r.first & 0xFFFu));
        }
        if (!entries.empty()) {
            Entry& p = entries.back();
            const bool phys_next = r.first - (p.phys + p.len) <= 16;
            const bool lin_next = p.has_lin == e.has_lin && (!e.has_lin || e.lin - p.lin == r.first - p.phys);
            if (phys_next && lin_next && p.more.empty() && e.more.empty()) {
                p.len = r.second - p.phys;
                continue;
            }
        }
        entries.push_back(e);
    }
    const bool real = !cpu.pmode || GETFLAG(VM);
    uint64_t total = 0;
    for (const Entry& e : entries) {
        if (total + e.len > (16ull << 20)) {
            *cut = true;
            break;
        }
        total += e.len;
        Address a;
        a.has_phys = true;
        a.phys = e.phys;
        if (!e.has_lin) {
            a.mode = "phys";
        } else {
            a.has_linear = true;
            a.linear = e.lin;
            if (real && e.lin < 0x10FFF0u) {
                // segment form, so that rel/symbol of 16-bit modules appear; the HMA only as FFFF:offset
                a.mode = GETFLAG(VM) ? "v86" : "real";
                a.has_selector = true;
                a.selector = e.lin < 0xFFFF0u ? static_cast<uint16_t>(e.lin >> 4) : 0xFFFFu;
                a.offset = e.lin - (static_cast<uint32_t>(a.selector) << 4);
                a.base = static_cast<uint32_t>(a.selector) << 4;
            } else {
                a.mode = "linear";
            }
        }
        JsonValue o = JsonValue::Object();
        const JsonValue addr = AddressJson(a, true);
        Set(&o, "address", addr);
        if (!e.more.empty()) {
            JsonValue more = JsonValue::Array();
            for (uint32_t m : e.more)
                more.array.push_back(HexOut(m, 8, true));
            Set(&o, "more_linear", more);
        }
        if (const JsonValue* sym = Param(addr, "symbol")) {
            const size_t plus = sym->text.find('+');
            Set(&o, "symbol_offset", Str(plus == std::string::npos ? "0x0" : sym->text.substr(plus + 1)));
        }
        Set(&o, "length", Num(e.len));
        Set(&o, "before", Str(HexUpper(&g.before[e.phys], e.len)));
        Set(&o, "after", Str(HexUpper(MemBase + e.phys, e.len)));
        list.array.push_back(o);
    }
    return list;
}

void Finish()
{
    if (!g.active)
        return;
    g.active = false;
    DMCP_irq_hold = false;
    JsonValue r = JsonValue::Object();
    Set(&r, "format", Str("dosbox-lab-func/1"));
    Set(&r, "state", Str(g.state));
    Set(&r, "call_id", Num(g.call_id));
    const std::string end = g.end.empty() ? "interrupted" : g.end;
    Set(&r, "end", Str(end));
    const std::string reason = g.stop_reason_override.empty() ? StopReason() : g.stop_reason_override;
    Set(&r, "stop_reason", end == "interrupted" ? Str(reason) : JsonValue());
    Set(&r, "instructions", Num(g.insn));
    Set(&r, "irqs", Bool(g.irqs));
    Set(&r, "regs_in", g.regs_in);
    Set(&r, "flags_in", g.flags_in);
    Set(&r, "regs_out", RegsJson());
    Set(&r, "flags_out", FlagsJson());
    Set(&r, "halt_addr", AddressJson(CodePointAddress(CurrentCodePoint()), true));
    Set(&r, "written", g.written);
    CloseStackVisit();
    bool cut = false;
    Set(&r, "changed", ChangedJson(&cut));
    Set(&r, "changed_truncated", Bool(cut));
    g.before.clear();
    g.before.shrink_to_fit();
    Set(&r, "ints", g.ints);
    Set(&r, "ints_omitted", Num(g.ints_dropped));
    const std::string text = dosbox_agent::AGENT_SerializeJson(r) + "\n";
    std::string path;
    Error ignored;
    if (WriteOutputFile(g.name + ".json", text.data(), text.size(), &path, &ignored))
        Set(&r, "path", Str(path));
    g.result = r;
    g.finished = true;
    UpdateHookActive();
}

} // namespace

bool FuncActive() { return g.active; }

bool FuncHaltsForever()
{
    // HLT waits for an IRQ that the run holds back: no instruction would ever execute again.
    if (!g.active || !g.end.empty() || !DMCP_irq_hold || !CPU_IsHLTed())
        return false;
    g.end = "hlt";
    return true;
}

bool FuncShouldStop()
{
    if (!g.active)
        return false;
    if (!g.end.empty())
        return true; // decided elsewhere (program end): stop at the next instruction
    if (static_cast<uint16_t>(SegValue(cs)) == g.ret_sel && reg_eip == g.ret_off &&
        static_cast<uint16_t>(SegValue(ss)) == g.ss && CurrentSp() >= g.sp_after) {
        g.end = EndFromArt(g.kind);
        return true;
    }
    if (g.insn >= g.max_insn) {
        g.end = "aborted";
        return true;
    }
    return false;
}

void FuncOnInstruction()
{
    if (!g.active)
        return;
    ++g.insn;
    TrackStack();
    if (!g.open_ints.empty()) {
        const uint16_t sel = static_cast<uint16_t>(SegValue(cs));
        const bool pm = cpu.pmode && !GETFLAG(VM);
        for (size_t i = g.open_ints.size(); i-- > 0;) {
            const Run::OpenInt& o = g.open_ints[i];
            if (o.cs != sel || o.ret_eip != reg_eip || o.pm != pm)
                continue;
            JsonValue& e = g.ints.array[o.index];
            Set(&e, "regs_out", RegsJson());
            Set(&e, "flags_out", FlagsJson());
            g.open_ints.erase(g.open_ints.begin() + static_cast<long>(i));
            break;
        }
    }
}

void FuncOnSoftwareInterrupt(uint8_t num, uint32_t oldeip)
{
    if (!g.active)
        return;
    const bool pm = cpu.pmode && !GETFLAG(VM);
    if (!pm && SegValue(cs) == CB_SEG)
        return; // DOSBox-internal call from a callback stub
    if (g.ints.array.size() >= 1000) {
        ++g.ints_dropped;
        return;
    }
    JsonValue o = JsonValue::Object();
    Set(&o, "vector", HexOut(num, 2, true));
    Set(&o, "addr", AddressJson(CodePointAddress(CurrentCodePoint()), true));
    Set(&o, "regs_in", RegsJson());
    Set(&o, "flags_in", FlagsJson());
    g.ints.array.push_back(o);
    g.open_ints.push_back({static_cast<uint16_t>(SegValue(cs)), oldeip, pm, g.ints.array.size() - 1});
}

void FuncOnStop() { Finish(); }

void FuncOnExit(uint16_t psp)
{
    // Only the program the function belongs to; a child started by the function may end on its own.
    if (g.active && g.end.empty() && psp == g.psp)
        g.end = "program_end";
}

void FuncOnStateLoad()
{
    if (!g.active)
        return;
    g.end = "interrupted";
    g.stop_reason_override = "state_load";
    g.before.clear(); // the comparison would compare two unrelated machine states
    Finish();
}

void RegisterFunc()
{
    Register("func.start", [](const JsonValue& params, JsonValue* result, Error* error) {
        // The return check sits in the instruction hook, which only the normal core calls.
        const Section_prop* cpu_section = static_cast<Section_prop*>(control->GetSection("cpu"));
        const std::string core = cpu_section != nullptr ? cpu_section->Get_string("core") : "";
        if (core != "normal")
            return Fail(error, "unsupported_core", "func.start needs core=normal (configured: core=" + core + ")");
        if (!RequireHookCore("func.start", error))
            return false;
        std::string state, name = "func";
        if (!ParamStr(params, "state", &state) || state.empty())
            return Fail(error, "bad_argument", "state (path of a .sav written by calls states) is required");
        if (Param(params, "name") != nullptr &&
            (!ParamStr(params, "name", &name) || !PlainName(name)))
            return Fail(error, "bad_argument", "name must be a plain file name without path characters");
        uint64_t max_insn = 10000000;
        if (Param(params, "max_instructions") != nullptr && (!ParamU64(params, "max_instructions", &max_insn) || max_insn == 0))
            return Fail(error, "bad_argument", "max_instructions must be at least 1");
        bool irqs = false;
        if (Param(params, "irqs") != nullptr && !ParamBool(params, "irqs", &irqs))
            return Fail(error, "bad_argument", "irqs must be true or false");
        // Check everything that does not need the loaded state first: a refused call leaves the machine alone.
        Run next;
        JsonValue sidecar;
        if (!ReadStateSidecar(state, &sidecar))
            return Fail(error, "bad_argument", "state has no readable sidecar (.json) with call: " + state);
        if (!ReadCall(sidecar, &next, error) || !ApplyInputs(params, false, error))
            return false;
        // An old run ends here (its result is dropped), then the state is loaded.
        g.active = false;
        g.finished = false;
        DMCP_irq_hold = false;
        UpdateHookActive();
        if (!LoadStateFile(state, false, &sidecar, error))
            return false;
        g = next;
        if (!ApplyInputs(params, true, error)) {
            StopAt("pause"); // the state is already replaced: keep it from running half prepared
            return false;
        }
        g.psp = dos.psp();
        g.state = state;
        g.name = name;
        g.max_insn = max_insn;
        g.irqs = irqs;
        g.min_sp = CurrentSp();
        g.cur_ss = g.ss;
        g.cur_base = static_cast<uint32_t>(SegPhys(ss));
        g.prev_cpl = cpu.cpl;
        g.regs_in = RegsJson();
        g.flags_in = FlagsJson();
        g.ss_base = static_cast<uint32_t>(SegPhys(ss));
        g.before.assign(MemBase, MemBase + static_cast<size_t>(GuestRamBytes()));
        DMCP_irq_hold = !irqs;
        g.active = true;
        UpdateHookActive();
        Resume(); // like emu.continue; a running emulator just keeps running
        Set(result, "running", Bool(true));
        return true;
    });
    Register("func.result", [](const JsonValue&, JsonValue* result, Error*) {
        if (g.finished) {
            *result = g.result;
            return true;
        }
        Set(result, "running", Bool(g.active));
        return true;
    });
}

} // namespace dmcp

#endif // C_DMCP
