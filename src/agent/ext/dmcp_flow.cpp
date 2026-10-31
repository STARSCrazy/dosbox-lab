// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "cpu.h"
#include "mem.h"
#include "paging.h"
#include "regs.h"
#include "dmcp_addr.h"
#include "dmcp_record.h"

#include <algorithm>
#include <map>
#include <tuple>
#include <vector>

namespace dmcp {
namespace {

enum class Kind : uint8_t {
    None, CallNear, JmpNear, CallFar, JmpFar, Ret, Retf, Iret, Call, Jmp, Jcc, CallFarDirect, JmpFarDirect,
    Int, Irq, Exception
};

const char* KindName(Kind k)
{
    switch (k) {
    case Kind::CallNear: return "call_near";
    case Kind::JmpNear: return "jmp_near";
    case Kind::CallFar: return "call_far";
    case Kind::JmpFar: return "jmp_far";
    case Kind::Ret: return "ret";
    case Kind::Retf: return "retf";
    case Kind::Iret: return "iret";
    case Kind::Call: return "call";
    case Kind::Jmp: return "jmp";
    case Kind::Jcc: return "jcc";
    case Kind::CallFarDirect: return "call_far_direct";
    case Kind::JmpFarDirect: return "jmp_far_direct";
    case Kind::Int: return "int";
    case Kind::Irq: return "irq";
    case Kind::Exception: return "exception";
    default: return "none";
    }
}

bool IsIndirect(Kind k) { return k == Kind::CallNear || k == Kind::JmpNear || k == Kind::CallFar || k == Kind::JmpFar; }
bool IsReturn(Kind k) { return k == Kind::Ret || k == Kind::Retf || k == Kind::Iret; }
bool HasVector(Kind k) { return k == Kind::Int || k == Kind::Irq || k == Kind::Exception; }

struct Decoded {
    Kind kind = Kind::None;
    uint32_t length = 0; // only needed to recognise "not taken" (Jcc/LOOP/INTO)
    uint8_t vector = 0;
};

struct Pending {
    bool active = false;
    Kind kind = Kind::None;
    uint8_t vector = 0;
    CodePoint src;
    uint32_t length = 0;
    uint16_t ss = 0;
    uint32_t sp = 0; // stack pointer before the transfer (masked by the stack size)
};

struct RingEntry {
    Kind kind;
    uint8_t vector;
    CodePoint src;
    CodePoint dst;
    uint64_t nr;
};

struct PairKey {
    uint8_t kind;
    uint32_t src;
    uint32_t dst;
    uint16_t src_sel;
    uint16_t dst_sel;
    bool operator<(const PairKey& o) const
    {
        return std::tie(kind, src, dst, src_sel, dst_sel) < std::tie(o.kind, o.src, o.dst, o.src_sel, o.dst_sel);
    }
};

struct PairValue {
    CodePoint src;
    CodePoint dst;
    uint64_t count = 0;
    uint64_t first = 0;
};

bool g_active = false;
bool g_indirect = true;
bool g_ring_on = true;
bool g_returns = false;
bool g_limit_hit = false;
size_t g_ring_size = 4096;
uint64_t g_max_pairs = 1000000;
std::vector<RingEntry> g_ring;
size_t g_ring_next = 0;
uint64_t g_ring_total = 0;
std::map<PairKey, PairValue> g_pairs;
Pending g_pending;
uint64_t g_insn = 0;

Decoded Decode(uint32_t linear, bool code32)
{
    Decoded d;
    bool op32 = code32;
    uint8_t b = 0;
    uint32_t i = 0;
    for (; i < 15; ++i) {
        if (!PeekLinear(linear + i, &b))
            return d;
        if (b == 0x66) {
            op32 = !code32;
            continue;
        }
        if (b == 0x67 || b == 0xF0 || b == 0xF2 || b == 0xF3 || b == 0x26 || b == 0x2E || b == 0x36 || b == 0x3E ||
            b == 0x64 || b == 0x65)
            continue;
        break;
    }
    if (i == 15)
        return d;
    uint8_t next = 0;
    switch (b) {
    case 0xFF:
        if (!PeekLinear(linear + i + 1, &next))
            return d;
        switch ((next >> 3) & 7) {
        case 2: d.kind = Kind::CallNear; break;
        case 3: d.kind = Kind::CallFar; break;
        case 4: d.kind = Kind::JmpNear; break;
        case 5: d.kind = Kind::JmpFar; break;
        default: break;
        }
        break;
    case 0xE8: d.kind = Kind::Call; break;
    case 0xE9: case 0xEB: d.kind = Kind::Jmp; break;
    case 0x9A: d.kind = Kind::CallFarDirect; break;
    case 0xEA: d.kind = Kind::JmpFarDirect; break;
    case 0xC2: case 0xC3: d.kind = Kind::Ret; break;
    case 0xCA: case 0xCB: d.kind = Kind::Retf; break;
    case 0xCF: d.kind = Kind::Iret; break;
    case 0xCC: d.kind = Kind::Int; d.vector = 3; d.length = i + 1; break;
    case 0xCE: d.kind = Kind::Int; d.vector = 4; d.length = i + 1; break;
    case 0xCD:
        if (!PeekLinear(linear + i + 1, &next))
            return d;
        d.kind = Kind::Int;
        d.vector = next;
        d.length = i + 2;
        break;
    case 0xE0: case 0xE1: case 0xE2: case 0xE3: d.kind = Kind::Jcc; d.length = i + 2; break;
    case 0x0F:
        if (PeekLinear(linear + i + 1, &next) && next >= 0x80 && next <= 0x8F) {
            d.kind = Kind::Jcc;
            d.length = i + 2 + (op32 ? 4 : 2);
        }
        break;
    default:
        if (b >= 0x70 && b <= 0x7F) {
            d.kind = Kind::Jcc;
            d.length = i + 2;
        }
        break;
    }
    return d;
}

void Resolve(const CodePoint& dst)
{
    const Pending p = g_pending;
    g_pending.active = false;
    const bool fallthrough = p.length != 0 && dst.linear == p.src.linear + p.length && dst.selector == p.src.selector;
    if (CoverageHitsOn())
        CoverageHit(dst.linear); // taken target or fall-through: start of a basic block
    if (fallthrough && (p.kind == Kind::Jcc || p.kind == Kind::Int))
        return; // not taken (Jcc, LOOP, JCXZ, INTO)
    if (CallsActive()) {
        bool report = true;
        Transfer t;
        switch (p.kind) {
        case Kind::Call: case Kind::CallNear: t.kind = TransferKind::CallNear; break;
        case Kind::CallFar: case Kind::CallFarDirect: t.kind = TransferKind::CallFar; break;
        case Kind::Int: t.kind = TransferKind::Int; break;
        case Kind::Irq: t.kind = TransferKind::Irq; break;
        case Kind::Exception: t.kind = TransferKind::Exception; break;
        case Kind::Ret: t.kind = TransferKind::Ret; break;
        case Kind::Retf: t.kind = TransferKind::Retf; break;
        case Kind::Iret: t.kind = TransferKind::Iret; break;
        default: report = false; break;
        }
        if (report) {
            t.vector = p.vector;
            t.src = p.src;
            t.dst = dst;
            t.ss_before = p.ss;
            t.sp_before = p.sp;
            CallsOnTransfer(t);
        }
    }
    if (!g_active)
        return;
    if (g_ring_on) {
        const RingEntry entry{p.kind, p.vector, p.src, dst, g_insn};
        if (g_ring.size() < g_ring_size)
            g_ring.push_back(entry);
        else
            g_ring[g_ring_next] = entry;
        g_ring_next = (g_ring_next + 1) % g_ring_size;
        ++g_ring_total;
    }
    if (g_indirect && (IsIndirect(p.kind) || (g_returns && IsReturn(p.kind)))) {
        const PairKey key{static_cast<uint8_t>(p.kind), p.src.linear, dst.linear, p.src.selector, dst.selector};
        auto it = g_pairs.find(key);
        if (it == g_pairs.end()) {
            if (g_pairs.size() >= g_max_pairs) {
                g_indirect = false;
                g_limit_hit = true;
                ReportLimit("flow", "max_pairs " + std::to_string(g_max_pairs));
                return;
            }
            PairValue value;
            value.src = p.src;
            value.dst = dst;
            value.first = g_insn;
            it = g_pairs.emplace(key, value).first;
        }
        ++it->second.count;
    }
}

JsonValue EntryJson(Kind kind, uint8_t vector, const CodePoint& src, const CodePoint& dst)
{
    JsonValue o = JsonValue::Object();
    Set(&o, "kind", Str(KindName(kind)));
    Set(&o, "source", AddressJson(CodePointAddress(src), true));
    Set(&o, "target", AddressJson(CodePointAddress(dst), true));
    Set(&o, "bits", Num(src.bits));
    if (HasVector(kind))
        Set(&o, "vector", HexOut(vector, 2, true));
    return o;
}

bool WriteLines(const std::string& file_name, const std::vector<JsonValue>& lines, std::string* path, Error* error)
{
    std::string text;
    for (const JsonValue& line : lines)
        text += dosbox_agent::AGENT_SerializeJson(line) + "\n";
    return WriteOutputFile(file_name, text.data(), text.size(), path, error);
}

JsonValue StatusJson()
{
    JsonValue r = JsonValue::Object();
    Set(&r, "active", Bool(g_active));
    Set(&r, "pairs", Num(g_pairs.size()));
    Set(&r, "ring_entries", Num(g_ring.size()));
    Set(&r, "ring_total", Num(g_ring_total));
    Set(&r, "instructions", Num(g_insn));
    Set(&r, "limit_reached_flag", Bool(g_limit_hit));
    return r;
}

bool OptionalBool(const JsonValue& params, const char* name, bool* out, Error* error)
{
    if (Param(params, name) != nullptr && !ParamBool(params, name, out))
        return Fail(error, "bad_argument", std::string(name) + " must be true or false");
    return true;
}

} // namespace

bool FlowEngineActive() { return g_active || CoverageHitsOn() || CallsActive(); }

void FlowResetIfIdle()
{
    if (!FlowEngineActive())
        g_pending.active = false;
}

void FlowOnStateLoad()
{
    g_pending.active = false; // the pending transfer belongs to the replaced state
}

void FlowOnInstruction(uint32_t linear)
{
    ++g_insn;
    const CodePoint here = CurrentCodePoint();
    if (g_pending.active)
        Resolve(here);
    const Decoded d = Decode(linear, cpu.code.big);
    if (d.kind == Kind::None)
        return;
    g_pending.active = true;
    g_pending.kind = d.kind;
    g_pending.vector = d.vector;
    g_pending.src = here;
    g_pending.length = d.length;
    g_pending.ss = static_cast<uint16_t>(SegValue(ss));
    g_pending.sp = reg_esp & cpu.stack.mask;
}

void FlowOnInterrupt(uint8_t num, unsigned type)
{
    if ((type & CPU_INT_SOFTWARE) != 0)
        return; // INT n: the decoded entry resolves at the handler's first instruction
    const CodePoint here = CurrentCodePoint();
    if (g_pending.active) {
        if ((type & CPU_INT_EXCEPTION) != 0 && here.linear == g_pending.src.linear && here.selector == g_pending.src.selector)
            g_pending.active = false; // fault inside that instruction: it did not complete
        else
            Resolve(here);
    }
    g_pending.active = true;
    g_pending.kind = (type & CPU_INT_EXCEPTION) != 0 ? Kind::Exception : Kind::Irq;
    g_pending.vector = num;
    g_pending.src = here;
    g_pending.length = 0;
    g_pending.ss = static_cast<uint16_t>(SegValue(ss));
    g_pending.sp = reg_esp & cpu.stack.mask;
}

void RegisterFlow()
{
    Register("flow.start", [](const JsonValue& params, JsonValue* result, Error* error) {
        bool indirect = true, ring = true, returns = false;
        uint64_t ring_size = 4096, max_pairs = 1000000;
        if (!OptionalBool(params, "indirect", &indirect, error) || !OptionalBool(params, "ring", &ring, error) ||
            !OptionalBool(params, "with_returns", &returns, error))
            return false;
        if (Param(params, "ring_size") != nullptr &&
            (!ParamU64(params, "ring_size", &ring_size) || ring_size == 0 || ring_size > 1000000))
            return Fail(error, "bad_argument", "ring_size must be between 1 and 1000000");
        if (Param(params, "max_pairs") != nullptr && (!ParamU64(params, "max_pairs", &max_pairs) || max_pairs == 0))
            return Fail(error, "bad_argument", "max_pairs must be positive");
        g_indirect = indirect;
        g_ring_on = ring;
        g_returns = returns;
        g_ring_size = static_cast<size_t>(ring_size);
        g_max_pairs = max_pairs;
        g_ring.clear();
        g_ring_next = 0;
        g_ring_total = 0;
        g_pairs.clear();
        g_pending.active = false;
        g_insn = 0;
        g_limit_hit = false;
        g_active = true;
        UpdateHookActive();
        *result = StatusJson();
        return true;
    });
    Register("flow.stop", [](const JsonValue&, JsonValue* result, Error*) {
        g_active = false;
        if (!FlowEngineActive())
            g_pending.active = false;
        UpdateHookActive();
        *result = StatusJson();
        return true;
    });
    Register("flow.status", [](const JsonValue&, JsonValue* result, Error*) {
        *result = StatusJson();
        return true;
    });
    Register("flow.save", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string name = "indirect";
        if (Param(params, "name") != nullptr && !ParamStr(params, "name", &name))
            return Fail(error, "bad_argument", "name must be a string");
        std::vector<std::pair<const PairKey*, const PairValue*>> ordered;
        for (const auto& entry : g_pairs)
            ordered.push_back(std::make_pair(&entry.first, &entry.second));
        std::stable_sort(ordered.begin(), ordered.end(),
                         [](const std::pair<const PairKey*, const PairValue*>& a,
                            const std::pair<const PairKey*, const PairValue*>& b) {
                             return a.second->first < b.second->first;
                         });
        std::vector<JsonValue> lines;
        for (const auto& item : ordered) {
            JsonValue line = EntryJson(static_cast<Kind>(item.first->kind), 0, item.second->src, item.second->dst);
            Set(&line, "count", Num(item.second->count));
            Set(&line, "first_nr", Num(item.second->first));
            lines.push_back(line);
        }
        std::string path;
        if (!WriteLines(name + ".jsonl", lines, &path, error))
            return false;
        Set(result, "path", Str(path));
        Set(result, "pairs", Num(lines.size()));
        return true;
    });
    Register("flow.recent", [](const JsonValue& params, JsonValue* result, Error* error) {
        uint64_t count = 100;
        std::string name = "recent";
        if (Param(params, "count") != nullptr && (!ParamU64(params, "count", &count) || count == 0))
            return Fail(error, "bad_argument", "count must be positive");
        if (Param(params, "name") != nullptr && !ParamStr(params, "name", &name))
            return Fail(error, "bad_argument", "name must be a string");
        const size_t have = g_ring.size();
        const size_t take = static_cast<size_t>(std::min<uint64_t>(count, have));
        const size_t oldest = have < g_ring_size ? 0 : g_ring_next; // index of the oldest entry
        std::vector<JsonValue> lines;
        for (size_t i = have - take; i < have; ++i) {
            const RingEntry& e = g_ring[(oldest + i) % have];
            JsonValue line = EntryJson(e.kind, e.vector, e.src, e.dst);
            Set(&line, "nr", Num(e.nr));
            lines.push_back(line);
        }
        std::string path;
        if (!WriteLines(name + ".jsonl", lines, &path, error))
            return false;
        Set(result, "path", Str(path));
        Set(result, "count", Num(lines.size()));
        return true;
    });
}

} // namespace dmcp

#endif // C_DMCP
