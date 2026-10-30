#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "cpu.h"
#include "mem.h"
#include "regs.h"
#include "dmcp_addr.h"
#include "dmcp_modules.h"
#include "dmcp_record.h"
#include "dmcp_state.h"

#include <algorithm>
#include <cstdio>
#include <fstream>
#include <map>
#include <set>
#include <vector>

Bitu FillFlags(void);

namespace dmcp {
namespace {

void StopCalls(bool limit);

struct Range {
    uint32_t from = 0, to = 0; // inclusive
    Address von, bis;
};

struct IntRule {
    uint8_t num = 0;
    int ah = -1;
};

struct Config {
    std::string name = "calls";
    std::vector<Address> ziele;
    std::set<uint32_t> ziel_set;
    std::vector<Range> bereiche;
    std::vector<std::string> module;
    std::vector<IntRule> ints;
    int stack_bytes = -1; // -1 = 32 (16-bit target) / 64 (32-bit target)
    uint64_t max_je_ziel = 1000;
    uint64_t max_bytes = 256ull << 20;
    uint64_t zustaende = 0;      // D31: savestate at the entry of the first N recorded calls per target
    uint64_t max_zustaende = 1000;
};

struct Frame {
    uint64_t id = 0;
    uint16_t ret_sel = 0;  // CS selector or real-mode segment to return to
    uint32_t ret_off = 0;
    uint16_t ss = 0;       // stack after the return: SS and at least sp_after
    uint32_t sp_after = 0;
    CodePoint target;
    JsonValue entry;       // fields measured at entry
};

struct Context {
    std::string name;      // "haupt", "irq 08", "exception 0E"
    uint16_t ret_sel = 0;  // where the interrupt returns to (not used for "haupt")
    uint32_t ret_off = 0;
    uint16_t ss = 0;
    uint32_t sp_after = 0;
    std::vector<Frame> frames;
};

const size_t kMaxFrames = 4096;
const size_t kMaxContexts = 64; // including the base context "haupt"

bool g_active = false;
bool g_limit_hit = false;
Config g_cfg;
std::ofstream g_file;
std::string g_path;
std::string g_json_path;
uint64_t g_bytes = 0;
uint64_t g_written = 0;
uint64_t g_insn = 0;
uint64_t g_next_id = 1;
std::vector<Context> g_contexts;
std::map<uint32_t, uint64_t> g_per_target;          // recorded entries per target linear
std::map<uint32_t, std::pair<CodePoint, uint64_t>> g_omitted; // target linear -> (point, count)
uint64_t g_states_written = 0;
uint64_t g_states_dropped = 0; // over max_zustaende

bool PlainName(const std::string& name)
{
    return !name.empty() && name.find_first_of("\\/:*?\"<>|") == std::string::npos;
}

bool LinearOf(const std::string& text, Address* out, Error* error)
{
    if (!ParseAddress(text, out, error))
        return false;
    if (!out->has_linear)
        return Fail(error, "bad_argument", "calls filter address needs a linear form (not p:): " + text);
    return true;
}

bool ParseConfig(const JsonValue& params, Config* cfg, Error* error)
{
    if (Param(params, "name") != nullptr && (!ParamStr(params, "name", &cfg->name) || !PlainName(cfg->name)))
        return Fail(error, "bad_argument", "name must be a plain file name without path characters");
    if (const JsonValue* list = Param(params, "ziele")) {
        if (list->type != JsonType::Array)
            return Fail(error, "bad_argument", "ziele must be a list of addresses");
        for (const JsonValue& item : list->array) {
            Address a;
            if (item.type != JsonType::String)
                return Fail(error, "bad_argument", "ziele entries must be address strings");
            if (!LinearOf(item.text, &a, error))
                return false;
            cfg->ziele.push_back(a);
            cfg->ziel_set.insert(a.linear);
        }
    }
    if (const JsonValue* list = Param(params, "bereiche")) {
        if (list->type != JsonType::Array)
            return Fail(error, "bad_argument", "bereiche must be a list of {von, bis}");
        for (const JsonValue& item : list->array) {
            std::string von, bis;
            if (item.type != JsonType::Object || !ParamStr(item, "von", &von) || !ParamStr(item, "bis", &bis))
                return Fail(error, "bad_argument", "bereiche entries need von and bis (address strings)");
            Range r;
            if (!LinearOf(von, &r.von, error) || !LinearOf(bis, &r.bis, error))
                return false;
            r.from = r.von.linear;
            r.to = r.bis.linear;
            if (r.to < r.from)
                return Fail(error, "bad_argument", "bereich bis lies before von");
            cfg->bereiche.push_back(r);
        }
    }
    if (const JsonValue* list = Param(params, "module")) {
        if (list->type != JsonType::Array)
            return Fail(error, "bad_argument", "module must be a list of module names");
        for (const JsonValue& item : list->array) {
            if (item.type != JsonType::String)
                return Fail(error, "bad_argument", "module entries must be strings");
            const Module* m = FindModule(Upper(item.text));
            if (m == nullptr)
                return Fail(error, "bad_argument", "unknown module " + item.text);
            bool any = false;
            for (const ModuleSegment& s : m->segments) {
                Range r;
                r.from = s.basis_linear + s.va_start;
                r.to = s.basis_linear + s.va_end;
                r.von.modus = r.bis.modus = "linear";
                r.von.has_linear = r.bis.has_linear = true;
                r.von.linear = r.from;
                r.bis.linear = r.to;
                cfg->bereiche.push_back(r);
                any = true;
            }
            if (!any && m->has_load && m->art != "overlay" && m->paragraphs != 0) {
                Range r;
                r.from = static_cast<uint32_t>(m->psp) << 4;
                r.to = ((static_cast<uint32_t>(m->psp) + m->paragraphs) << 4) - 1;
                r.von.modus = r.bis.modus = "linear";
                r.von.has_linear = r.bis.has_linear = true;
                r.von.linear = r.from;
                r.bis.linear = r.to;
                cfg->bereiche.push_back(r);
                any = true;
            }
            if (!any)
                return Fail(error, "bad_argument", "module " + item.text + " has no address mapping");
            cfg->module.push_back(m->name);
        }
    }
    if (const JsonValue* list = Param(params, "ints")) {
        if (list->type != JsonType::Array)
            return Fail(error, "bad_argument", "ints must be a list of {nummer, ah?}");
        for (const JsonValue& entry : list->array) {
            IntRule rule;
            uint32_t value = 0;
            if (entry.type != JsonType::Object || !HexField(entry, "nummer", &value) || value > 0xFF)
                return Fail(error, "bad_argument", "ints entries need nummer (hex string, 00-FF)");
            rule.num = static_cast<uint8_t>(value);
            if (Param(entry, "ah") != nullptr) {
                if (!HexField(entry, "ah", &value) || value > 0xFF)
                    return Fail(error, "bad_argument", "ints ah must be a hex byte");
                rule.ah = static_cast<int>(value);
            }
            cfg->ints.push_back(rule);
        }
    }
    if (cfg->ziele.empty() && cfg->bereiche.empty() && cfg->ints.empty())
        return Fail(error, "bad_argument", "calls.start needs ziele, bereiche, module or ints");
    uint64_t value = 0;
    if (Param(params, "stack_bytes") != nullptr) {
        if (!ParamU64(params, "stack_bytes", &value) || value > 1024)
            return Fail(error, "bad_argument", "stack_bytes must be between 0 and 1024");
        cfg->stack_bytes = static_cast<int>(value);
    }
    if (Param(params, "max_je_ziel") != nullptr &&
        (!ParamU64(params, "max_je_ziel", &cfg->max_je_ziel) || cfg->max_je_ziel == 0))
        return Fail(error, "bad_argument", "max_je_ziel must be at least 1");
    if (Param(params, "max_bytes") != nullptr && (!ParamU64(params, "max_bytes", &cfg->max_bytes) ||
                                                  cfg->max_bytes < 4096))
        return Fail(error, "bad_argument", "max_bytes must be at least 4096");
    if (Param(params, "zustaende") != nullptr && !ParamU64(params, "zustaende", &cfg->zustaende))
        return Fail(error, "bad_argument", "zustaende must be a number >= 0");
    if (Param(params, "max_zustaende") != nullptr &&
        (!ParamU64(params, "max_zustaende", &cfg->max_zustaende) || cfg->max_zustaende == 0))
        return Fail(error, "bad_argument", "max_zustaende must be at least 1");
    return true;
}

uint64_t OpenCount()
{
    uint64_t n = 0;
    for (const Context& c : g_contexts)
        n += c.frames.size();
    return n;
}

uint64_t OmittedTotal()
{
    uint64_t n = 0;
    for (const auto& e : g_omitted)
        n += e.second.second;
    return n;
}

JsonValue StatusJson()
{
    JsonValue r = JsonValue::Object();
    Set(&r, "aktiv", Bool(g_active));
    Set(&r, "datei", Str(g_path));
    Set(&r, "geschrieben", Num(g_written));
    Set(&r, "offen", Num(OpenCount()));
    Set(&r, "weggelassen", Num(OmittedTotal()));
    Set(&r, "bytes", Num(g_bytes));
    Set(&r, "limit_erreicht", Bool(g_limit_hit));
    Set(&r, "zustaende_geschrieben", Num(g_states_written));
    Set(&r, "zustaende_weggelassen", Num(g_states_dropped));
    return r;
}

JsonValue MetaJson()
{
    JsonValue filter = JsonValue::Object();
    JsonValue ziele = JsonValue::Array();
    for (const Address& a : g_cfg.ziele)
        ziele.array.push_back(AddressJson(a, true));
    Set(&filter, "ziele", ziele);
    JsonValue bereiche = JsonValue::Array();
    for (const Range& r : g_cfg.bereiche) {
        JsonValue o = JsonValue::Object();
        Set(&o, "von", AddressJson(r.von, true));
        Set(&o, "bis", AddressJson(r.bis, true));
        bereiche.array.push_back(o);
    }
    Set(&filter, "bereiche", bereiche);
    JsonValue module = JsonValue::Array();
    for (const std::string& m : g_cfg.module)
        module.array.push_back(Str(m));
    Set(&filter, "module", module);
    JsonValue ints = JsonValue::Array();
    for (const IntRule& rule : g_cfg.ints) {
        JsonValue o = JsonValue::Object();
        Set(&o, "nummer", HexOut(rule.num, 2, true));
        if (rule.ah >= 0)
            Set(&o, "ah", HexOut(static_cast<uint64_t>(rule.ah), 2, true));
        ints.array.push_back(o);
    }
    Set(&filter, "ints", ints);
    JsonValue meta = JsonValue::Object();
    Set(&meta, "format", Str("dosbox-mcp-calls/1"));
    Set(&meta, "datei", Str(g_cfg.name + ".jsonl"));
    Set(&meta, "filter", filter);
    JsonValue stack_bytes;
    if (g_cfg.stack_bytes >= 0)
        stack_bytes = Num(static_cast<uint64_t>(g_cfg.stack_bytes));
    Set(&meta, "stack_bytes", stack_bytes); // default JsonValue is null
    Set(&meta, "max_je_ziel", Num(g_cfg.max_je_ziel));
    Set(&meta, "geschrieben", Num(g_written));
    Set(&meta, "offen", Num(OpenCount()));
    Set(&meta, "befehle", Num(g_insn));
    Set(&meta, "limit_erreicht", Bool(g_limit_hit));
    Set(&meta, "zustaende", Num(g_cfg.zustaende));
    Set(&meta, "max_zustaende", Num(g_cfg.max_zustaende));
    Set(&meta, "zustaende_geschrieben", Num(g_states_written));
    Set(&meta, "zustaende_weggelassen", Num(g_states_dropped));
    JsonValue omitted = JsonValue::Array();
    for (const auto& e : g_omitted) {
        JsonValue o = JsonValue::Object();
        Set(&o, "ziel", AddressJson(CodePointAddress(e.second.first), true));
        Set(&o, "anzahl", Num(e.second.second));
        omitted.array.push_back(o);
    }
    Set(&meta, "weggelassen", omitted);
    return meta;
}

const char* EndName(TransferKind k)
{
    switch (k) {
    case TransferKind::Ret: return "ret";
    case TransferKind::Retf: return "retf";
    default: return "iret";
    }
}

uint32_t CurrentSp() { return reg_esp & cpu.stack.mask; }

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

std::string HexBytes(const uint8_t* data, size_t len)
{
    static const char digits[] = "0123456789ABCDEF";
    std::string out;
    out.reserve(len * 2);
    for (size_t i = 0; i < len; ++i) {
        out.push_back(digits[data[i] >> 4]);
        out.push_back(digits[data[i] & 15]);
    }
    return out;
}

// n bytes from SS:(E)SP + skip; *len = readable bytes before the first missing one and, on a 16-bit stack,
// before the segment end (offset 10000h).
std::vector<uint8_t> ReadStack(uint32_t skip, size_t n, size_t* len)
{
    std::vector<uint8_t> raw(n);
    const uint32_t offset = (CurrentSp() + skip) & cpu.stack.mask;
    const Address a = SegCacheAddress(ss, offset);
    RangeList missing;
    ReadAddress(a, raw.data(), n, &missing);
    *len = n;
    if (!cpu.stack.big)
        *len = std::min<size_t>(*len, 0x10000u - offset);
    for (const auto& r : missing)
        if (a.has_linear && r.first >= a.linear)
            *len = std::min<size_t>(*len, r.first - a.linear);
        else
            *len = 0;
    return raw;
}

uint32_t StackValue(uint32_t skip, int width)
{
    size_t len = 0;
    const std::vector<uint8_t> raw = ReadStack(skip, static_cast<size_t>(width), &len);
    uint32_t v = 0;
    for (int i = width - 1; i >= 0; --i)
        v = (v << 8) | raw[static_cast<size_t>(i)];
    return v;
}

bool WriteLine(const JsonValue& line)
{
    if (!g_active)
        return false;
    const std::string text = dosbox_agent::AGENT_SerializeJson(line) + "\n";
    if (g_bytes + text.size() > g_cfg.max_bytes) {
        ReportLimit("calls", "max_bytes " + std::to_string(g_cfg.max_bytes));
        StopCalls(true);
        return false;
    }
    g_file << text;
    g_bytes += text.size();
    ++g_written;
    return true;
}

void WriteClosed(Frame& f, const char* ende, bool with_exit)
{
    JsonValue line = f.entry;
    Set(&line, "nr_ende", Num(g_insn));
    if (with_exit) {
        Set(&line, "regs_aus", RegsJson());
        Set(&line, "flags_aus", FlagsJson());
    }
    Set(&line, "ende", Str(ende));
    WriteLine(line);
}

void WriteOpen(const Frame& f)
{
    JsonValue line = f.entry;
    Set(&line, "offen", Bool(true));
    WriteLine(line);
}

// All frames of context c from index `from` upward leave without their own return.
void DropFrom(Context& c, size_t from)
{
    while (c.frames.size() > from) {
        WriteClosed(c.frames.back(), "unbekannt", false);
        if (!g_active)
            return;
        c.frames.pop_back();
    }
}

bool ReturnsHere(uint16_t ret_sel, uint32_t ret_off, uint16_t frame_ss, uint32_t sp_after)
{
    return static_cast<uint16_t>(SegValue(cs)) == ret_sel && reg_eip == ret_off &&
           static_cast<uint16_t>(SegValue(ss)) == frame_ss && CurrentSp() >= sp_after;
}

bool Wanted(const Transfer& t)
{
    if (t.kind == TransferKind::Int) {
        for (const IntRule& rule : g_cfg.ints)
            if (rule.num == t.vector && (rule.ah < 0 || rule.ah == reg_ah))
                return true;
        return false;
    }
    if (g_cfg.ziel_set.count(t.dst.linear) != 0)
        return true;
    for (const Range& r : g_cfg.bereiche)
        if (t.dst.linear >= r.from && t.dst.linear <= r.to)
            return true;
    return false;
}

bool HasErrorCode(uint8_t vector)
{
    return cpu.pmode && (vector == 8 || (vector >= 10 && vector <= 14) || vector == 17);
}

// Return address and stack level after the return, measured at the first instruction of the target.
void ReturnPoint(const Transfer& t, uint16_t* sel, uint32_t* off, uint16_t* frame_ss, uint32_t* sp_after)
{
    const bool same_stack = static_cast<uint16_t>(SegValue(ss)) == t.ss_before;
    const uint32_t pushed = same_stack ? ((t.sp_before - CurrentSp()) & cpu.stack.mask) : 0;
    int width = t.src.bits == 32 ? 4 : 2;
    uint32_t skip = 0;
    switch (t.kind) {
    case TransferKind::CallNear:
        if (pushed == 2 || pushed == 4)
            width = static_cast<int>(pushed);
        *off = StackValue(0, width);
        *sel = t.src.selector;
        break;
    default: // far call, int, irq, exception: offset, then selector (and flags)
        if (t.kind == TransferKind::CallFar && (pushed == 4 || pushed == 8))
            width = static_cast<int>(pushed / 2);
        else if (t.kind != TransferKind::CallFar)
            width = same_stack ? (pushed >= 12 ? 4 : 2) : (t.dst.bits == 32 ? 4 : 2);
        if (t.kind == TransferKind::Exception && HasErrorCode(t.vector))
            skip = static_cast<uint32_t>(width);
        *off = StackValue(skip, width);
        *sel = static_cast<uint16_t>(StackValue(skip + static_cast<uint32_t>(width), 2));
        break;
    }
    *frame_ss = t.ss_before;
    *sp_after = t.sp_before;
}

// D31: savestate at the first instruction of the target (before it executes); the sidecar names the return.
void SaveEntryState(Frame& f, const Transfer& t, int bits)
{
    if (g_states_written >= g_cfg.max_zustaende) {
        ++g_states_dropped;
        return;
    }
    JsonValue aufruf = JsonValue::Object();
    Set(&aufruf, "call_id", Num(f.id));
    Set(&aufruf, "calls_datei", Str(g_cfg.name + ".jsonl"));
    Set(&aufruf, "art", Str(t.kind == TransferKind::CallNear ? "near" : (t.kind == TransferKind::CallFar ? "far" : "int")));
    Set(&aufruf, "bits", Num(static_cast<uint64_t>(bits)));
    Set(&aufruf, "ziel", AddressJson(CodePointAddress(t.dst), true));
    JsonValue ret = JsonValue::Object();
    Set(&ret, "sel", HexOut(f.ret_sel, 4, true));
    Set(&ret, "off", HexOut(f.ret_off, 8, true));
    Set(&aufruf, "ruecksprung", ret);
    Set(&aufruf, "ss", HexOut(f.ss, 4, true));
    Set(&aufruf, "esp_ein", HexOut(CurrentSp(), 8, true));
    Set(&aufruf, "esp_nach", HexOut(f.sp_after, 8, true));
    Set(&aufruf, "modus", Str(t.dst.modus == 2 ? "pm" : (t.dst.modus == 1 ? "v86" : "real")));
    std::string path, info;
    uint64_t size = 0;
    Error err;
    if (SaveStateFile(g_cfg.name + "_" + std::to_string(f.id), &aufruf, &path, &info, &size, &err)) {
        Set(&f.entry, "zustand", Str(path));
        ++g_states_written;
    } else {
        Set(&f.entry, "zustand_fehler", Str(err.message));
    }
}

void OnEntry(const Transfer& t)
{
    Context& c = g_contexts.back();
    const uint32_t key = t.dst.linear;
    uint64_t nth = 0; // number of this recorded call of the target (1 = first)
    {
        uint64_t& count = g_per_target[key]; // the limit applies to every kind, INT keyed by its handler
        if (count >= g_cfg.max_je_ziel) {
            auto& o = g_omitted[key];
            o.first = t.dst;
            ++o.second;
            return;
        }
        nth = ++count;
    }
    if (c.frames.size() >= kMaxFrames) {
        WriteClosed(c.frames.front(), "unbekannt", false);
        if (!g_active)
            return; // size limit: StopCalls cleared the contexts
        c.frames.erase(c.frames.begin());
    }
    Frame f;
    f.id = g_next_id++;
    f.target = t.dst;
    ReturnPoint(t, &f.ret_sel, &f.ret_off, &f.ss, &f.sp_after);
    const int bits = t.dst.bits == 32 ? 32 : 16;
    const size_t want = g_cfg.stack_bytes >= 0 ? static_cast<size_t>(g_cfg.stack_bytes) : (bits == 32 ? 64 : 32);
    size_t got = 0;
    const std::vector<uint8_t> stack = ReadStack(0, want, &got);
    JsonValue e = JsonValue::Object();
    Set(&e, "call_id", Num(f.id));
    if (c.frames.empty())
        Set(&e, "parent_id", JsonValue()); // null
    else
        Set(&e, "parent_id", Num(c.frames.back().id));
    Set(&e, "tiefe", Num(c.frames.size()));
    Set(&e, "kontext", Str(c.name));
    Set(&e, "nr", Num(g_insn));
    Set(&e, "von", AddressJson(CodePointAddress(t.src), true));
    Set(&e, "ziel", AddressJson(CodePointAddress(t.dst), true));
    Set(&e, "art", Str(t.kind == TransferKind::CallNear ? "near" : (t.kind == TransferKind::CallFar ? "far" : "int")));
    if (t.kind == TransferKind::Int)
        Set(&e, "vektor", HexOut(t.vector, 2, true));
    Set(&e, "bits", Num(static_cast<uint64_t>(bits)));
    Set(&e, "regs_ein", RegsJson());
    Set(&e, "flags_ein", FlagsJson());
    Set(&e, "stack", Str(HexBytes(stack.data(), got)));
    Set(&e, "stack_laenge", Num(got));
    f.entry = e;
    if (g_cfg.zustaende != 0 && nth <= g_cfg.zustaende)
        SaveEntryState(f, t, bits);
    c.frames.push_back(f);
}

// Context i (not the base) and all contexts above it end: their frames are written as "unbekannt".
void DropContextsFrom(size_t i)
{
    while (g_contexts.size() > i) {
        DropFrom(g_contexts.back(), 0);
        if (!g_active)
            return;
        g_contexts.pop_back();
    }
}

void OnInterruptContext(const Transfer& t)
{
    if (g_contexts.size() >= kMaxContexts) {
        // Handlers that never IRET would grow the stack without bound: the oldest interrupt context goes first.
        DropFrom(g_contexts[1], 0);
        if (!g_active)
            return;
        g_contexts.erase(g_contexts.begin() + 1);
    }
    Context ctx;
    char name[24];
    snprintf(name, sizeof(name), "%s %02X", t.kind == TransferKind::Irq ? "irq" : "exception",
             static_cast<unsigned>(t.vector));
    ctx.name = name;
    ReturnPoint(t, &ctx.ret_sel, &ctx.ret_off, &ctx.ss, &ctx.sp_after);
    g_contexts.push_back(ctx);
}

void OnReturn(const Transfer& t)
{
    Context& c = g_contexts.back();
    for (size_t i = c.frames.size(); i-- > 0;) {
        const Frame& f = c.frames[i];
        if (!ReturnsHere(f.ret_sel, f.ret_off, f.ss, f.sp_after))
            continue;
        DropFrom(c, i + 1);
        if (!g_active)
            return;
        WriteClosed(c.frames.back(), EndName(t.kind), true);
        if (!g_active)
            return;
        c.frames.pop_back();
        return;
    }
    // A return that reaches the return point of an interrupt context (IRET, or RET/RETF of a handler that
    // leaves without IRET) closes the innermost such context; contexts above it end with it.
    for (size_t i = g_contexts.size(); i-- > 1;) {
        const Context& x = g_contexts[i];
        if (ReturnsHere(x.ret_sel, x.ret_off, x.ss, x.sp_after)) {
            DropContextsFrom(i);
            return;
        }
    }
}

void WriteOpenFrames()
{
    // Index loops: a size limit inside WriteOpen stops the recording and clears g_contexts.
    for (size_t i = 0; g_active && i < g_contexts.size(); ++i)
        for (size_t j = 0; g_active && j < g_contexts[i].frames.size(); ++j)
            WriteOpen(g_contexts[i].frames[j]);
}

// Finishes the recording: open frames as "offen" (not after a size limit: the file is full), side file, close.
void StopCalls(bool limit)
{
    if (!g_active)
        return;
    if (!limit) {
        WriteOpenFrames(); // may hit the size limit: WriteLine then calls StopCalls(true), which finishes
        if (!g_active)
            return;
    }
    g_active = false;
    g_limit_hit = g_limit_hit || limit;
    g_file.flush();
    g_file.close();
    const std::string text = dosbox_agent::AGENT_SerializeJson(MetaJson()) + "\n";
    Error ignored;
    WriteOutputFile(g_cfg.name + ".json", text.data(), text.size(), &g_json_path, &ignored);
    g_contexts.clear(); // the stopped run keeps no open frames (status, a second calls.stop)
    UpdateHookActive();
    FlowResetIfIdle(); // a pending transfer must not resolve into a later calls run
}

} // namespace

bool CallsActive() { return g_active; }
void CallsOnInstruction() { ++g_insn; }

// Program end: interrupt contexts end with it (a handler that exits never IRETs), then the frames of the base
// context that return into the ended program (or any protected-mode frame) and everything above them.
void CallsOnExit(uint16_t psp)
{
    if (!g_active || g_contexts.empty())
        return;
    DropContextsFrom(1);
    if (!g_active)
        return;
    const uint32_t paragraphs = real_readw(static_cast<uint16_t>(psp - 1), 3); // MCB size
    Context& c = g_contexts.front();
    for (size_t i = 0; i < c.frames.size(); ++i) {
        const Frame& f = c.frames[i];
        const bool pm = f.target.modus == 2;
        const bool own = f.ret_sel >= psp && f.ret_sel < psp + paragraphs;
        if (pm || own) {
            DropFrom(c, i);
            return;
        }
    }
}

// The loaded state replaces the call stack: every open frame ends as "unbekannt".
void CallsOnStateLoad()
{
    if (!g_active || g_contexts.empty())
        return;
    DropContextsFrom(1);
    if (g_active)
        DropFrom(g_contexts.front(), 0);
}

void CallsOnTransfer(const Transfer& t)
{
    if (!g_active || g_contexts.empty())
        return;
    switch (t.kind) {
    case TransferKind::CallNear:
    case TransferKind::CallFar:
    case TransferKind::Int:
        if (Wanted(t))
            OnEntry(t);
        break;
    case TransferKind::Irq:
    case TransferKind::Exception:
        OnInterruptContext(t);
        break;
    default:
        OnReturn(t);
        break;
    }
}

uint64_t CallsCurrentId()
{
    if (!g_active || g_contexts.empty() || g_contexts.back().frames.empty())
        return 0;
    return g_contexts.back().frames.back().id;
}

void RegisterCalls()
{
    Register("calls.start", [](const JsonValue& params, JsonValue* result, Error* error) {
        Config cfg;
        if (!ParseConfig(params, &cfg, error))
            return false;
        if (g_active)
            StopCalls(false);
        g_path = OutputPath(cfg.name + ".jsonl");
        g_json_path = OutputPath(cfg.name + ".json");
        g_file.open(g_path, std::ios::binary | std::ios::trunc);
        if (!g_file)
            return Fail(error, "internal", "cannot open " + g_path);
        g_cfg = cfg;
        g_limit_hit = false;
        g_bytes = 0;
        g_written = 0;
        g_insn = 0;
        g_next_id = 1;
        g_per_target.clear();
        g_omitted.clear();
        g_states_written = 0;
        g_states_dropped = 0;
        g_contexts.clear();
        Context haupt;
        haupt.name = "haupt";
        g_contexts.push_back(haupt);
        FlowResetIfIdle(); // drop a transfer left pending by an earlier run (no consumer active right now)
        g_active = true;
        UpdateHookActive();
        *result = StatusJson();
        return true;
    });
    Register("calls.stop", [](const JsonValue&, JsonValue* result, Error*) {
        const uint64_t offen = OpenCount();
        StopCalls(false);
        *result = StatusJson();
        Set(result, "json_pfad", Str(g_json_path));
        Set(result, "offen", Num(offen));
        return true;
    });
    Register("calls.status", [](const JsonValue&, JsonValue* result, Error*) {
        *result = StatusJson();
        return true;
    });
}

} // namespace dmcp

#endif // C_DMCP
