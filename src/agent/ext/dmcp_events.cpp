// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "agent/dmcp.h"
#include "cpu.h"
#include "mem.h"
#include "pic.h"
#include "regs.h"
#include "dmcp_addr.h"
#include "dmcp_modules.h"
#include "dmcp_record.h"

#include <cstdio>
#include <cstdlib>
#include <deque>
#include <fstream>
#include <set>
#include <vector>

namespace dmcp {
namespace {

struct IntRule {
    uint8_t num = 0;
    int ah = -1; // -1 = any
    int ax = -1;
};

struct Config {
    std::set<std::string> types; // empty = all
    EventTypes flags;
    std::vector<IntRule> int_rules;
    std::string int_mode = "all"; // "all", "pm", "real"
    bool int_returns = false;
    uint64_t max_bytes = 256ull << 20;
    uint32_t file_preview = 16;
};

const char* const kTypes[] = {"exec", "exit", "int", "int_ret", "mode", "new_code_selector", "limit_reached", "breakpoint", "watch", "file"};
const size_t kRingSize = 10000;

bool g_active = false;
bool g_limit_hit = false;
Config g_cfg;
std::ofstream g_file;
std::string g_path;
uint64_t g_bytes = 0;
uint64_t g_count = 0;
uint64_t g_nr = 0;
uint64_t g_generation = 0;
unsigned g_unflushed = 0;
std::deque<JsonValue> g_ring; // last kRingSize events for events.read

bool KnownType(const std::string& type)
{
    for (const char* known : kTypes)
        if (type == known)
            return true;
    return false;
}

bool TypeOn(const std::string& type)
{
    return type == "limit_reached" || g_cfg.types.empty() || g_cfg.types.count(type) != 0;
}

void StopEvents(bool limit)
{
    if (g_active) {
        g_file.flush();
        g_file.close();
    }
    g_active = false;
    g_limit_hit = g_limit_hit || limit;
    UpdateHookActive();
}

void WriteEvent(const std::string& type, JsonValue event)
{
    Set(&event, "nr", Num(++g_nr));
    Set(&event, "type", Str(type));
    Set(&event, "time_ms", NumFixed(PIC_FullIndex()));
    if (event.Find("addr") == nullptr)
        Set(&event, "addr", AddressJson(SegCacheAddress(cs, reg_eip), true));
    const std::string line = dosbox_agent::AGENT_SerializeJson(event) + "\n";
    if (type != "limit_reached" && g_bytes + line.size() > g_cfg.max_bytes) {
        --g_nr; // the dropped event keeps no number
        JsonValue limit = JsonValue::Object();
        Set(&limit, "recording", Str("events"));
        Set(&limit, "detail", Str("max_bytes " + std::to_string(g_cfg.max_bytes)));
        WriteEvent("limit_reached", limit);
        StopEvents(true);
        return;
    }
    g_file << line;
    g_bytes += line.size();
    ++g_count;
    g_ring.push_back(event);
    if (g_ring.size() > kRingSize)
        g_ring.pop_front();
    if (++g_unflushed >= 64) {
        g_file.flush();
        g_unflushed = 0;
    }
}

JsonValue StatusJson()
{
    JsonValue r = JsonValue::Object();
    Set(&r, "active", Bool(g_active));
    Set(&r, "file", Str(g_path));
    Set(&r, "count", Num(g_count));
    Set(&r, "bytes", Num(g_bytes));
    Set(&r, "max_bytes", Num(g_cfg.max_bytes));
    Set(&r, "last_nr", Num(g_nr));
    Set(&r, "limit_reached_flag", Bool(g_limit_hit));
    JsonValue types = JsonValue::Array();
    for (const char* known : kTypes)
        if (TypeOn(known))
            types.array.push_back(Str(known));
    Set(&r, "types", types);
    return r;
}

bool ParseConfig(const JsonValue& params, Config* cfg, Error* error)
{
    const JsonValue* types = Param(params, "types");
    if (types != nullptr) {
        if (types->type != JsonType::Array)
            return Fail(error, "bad_argument", "types must be a list");
        for (const JsonValue& t : types->array) {
            if (t.type != JsonType::String || !KnownType(t.text))
                return Fail(error, "bad_argument", "unknown event type (exec, exit, int, int_ret, mode, "
                                                   "new_code_selector, limit_reached, breakpoint, watch, file)");
            cfg->types.insert(t.text);
        }
    }
    const JsonValue* rules = Param(params, "int_filter");
    if (rules != nullptr) {
        if (rules->type != JsonType::Array)
            return Fail(error, "bad_argument", "int_filter must be a list of {number, ah?, ax?}");
        for (const JsonValue& entry : rules->array) {
            IntRule rule;
            uint32_t value = 0;
            if (entry.type != JsonType::Object || !HexField(entry, "number", &value) || value > 0xFF)
                return Fail(error, "bad_argument", "int_filter entries need number (hex string, 00-FF)");
            rule.num = static_cast<uint8_t>(value);
            if (Param(entry, "ah") != nullptr) {
                if (!HexField(entry, "ah", &value) || value > 0xFF)
                    return Fail(error, "bad_argument", "int_filter ah must be a hex byte");
                rule.ah = static_cast<int>(value);
            }
            if (Param(entry, "ax") != nullptr) {
                if (!HexField(entry, "ax", &value) || value > 0xFFFF)
                    return Fail(error, "bad_argument", "int_filter ax must be a hex word");
                rule.ax = static_cast<int>(value);
            }
            cfg->int_rules.push_back(rule);
        }
    } else {
        for (int num : {0x21, 0x2F, 0x31, 0x33}) {
            IntRule rule;
            rule.num = static_cast<uint8_t>(num);
            cfg->int_rules.push_back(rule);
        }
    }
    if (Param(params, "int_mode") != nullptr) {
        if (!ParamStr(params, "int_mode", &cfg->int_mode) ||
            (cfg->int_mode != "all" && cfg->int_mode != "pm" && cfg->int_mode != "real"))
            return Fail(error, "bad_argument", "int_mode must be all, pm or real");
    }
    if (Param(params, "with_return") != nullptr && !ParamBool(params, "with_return", &cfg->int_returns))
        return Fail(error, "bad_argument", "with_return must be true or false");
    if (Param(params, "max_bytes") != nullptr && (!ParamU64(params, "max_bytes", &cfg->max_bytes) ||
                                                  cfg->max_bytes < 4096))
        return Fail(error, "bad_argument", "max_bytes must be at least 4096");
    if (Param(params, "file_preview") != nullptr) {
        uint64_t v = 0;
        if (!ParamU64(params, "file_preview", &v) || v > 256)
            return Fail(error, "bad_argument", "file_preview must be between 0 and 256");
        cfg->file_preview = static_cast<uint32_t>(v);
    }
    cfg->flags.exec = cfg->types.empty() || cfg->types.count("exec") != 0;
    cfg->flags.exit = cfg->types.empty() || cfg->types.count("exit") != 0;
    cfg->flags.int_ = cfg->types.empty() || cfg->types.count("int") != 0;
    cfg->flags.int_ret = cfg->types.empty() || cfg->types.count("int_ret") != 0;
    cfg->flags.mode = cfg->types.empty() || cfg->types.count("mode") != 0;
    cfg->flags.new_code_selector = cfg->types.empty() || cfg->types.count("new_code_selector") != 0;
    cfg->flags.file = cfg->types.empty() || cfg->types.count("file") != 0;
    return true;
}


std::string SegOff(uint32_t realpt)
{
    char text[16];
    snprintf(text, sizeof(text), "%04X:%04X", static_cast<unsigned>(realpt >> 16),
                  static_cast<unsigned>(realpt & 0xFFFFu));
    return text;
}

} // namespace

bool EventsActive() { return g_active; }
const EventTypes& ActiveEventTypes() { return g_cfg.flags; }
uint64_t EventsGeneration() { return g_generation; }
uint64_t LastEventNr() { return g_nr; }
bool IntReturnsWanted() { return g_cfg.int_returns; }
uint32_t FilePreview() { return g_cfg.file_preview; }

void EmitEvent(const std::string& type, JsonValue event)
{
    if (!g_active || !TypeOn(type))
        return;
    WriteEvent(type, event);
}

bool IntWanted(uint8_t num, uint16_t ax, bool pm)
{
    if (!g_active || !g_cfg.flags.int_)
        return false;
    if ((g_cfg.int_mode == "pm" && !pm) || (g_cfg.int_mode == "real" && pm))
        return false;
    for (const IntRule& rule : g_cfg.int_rules)
        if (rule.num == num && (rule.ah < 0 || rule.ah == (ax >> 8)) && (rule.ax < 0 || rule.ax == ax))
            return true;
    return false;
}

void ReportLimit(const std::string& recording, const std::string& detail)
{
    if (!g_active)
        return;
    JsonValue event = JsonValue::Object();
    Set(&event, "recording", Str(recording));
    Set(&event, "detail", Str(detail));
    WriteEvent("limit_reached", event);
}

void EventOnExec(const std::string& path, uint16_t flags, bool com, uint16_t psp, uint16_t load_seg,
                 uint16_t reloc, uint32_t paragraphs, uint32_t csip, uint32_t sssp)
{
    if (!g_active || !g_cfg.flags.exec)
        return;
    const bool overlay = flags == 3;
    JsonValue event = JsonValue::Object();
    Set(&event, "file", Str(path));
    Set(&event, "al", HexOut(flags, 2, true));
    Set(&event, "kind", Str(overlay ? "overlay" : (com ? "com" : "exe")));
    Set(&event, "module", Str(ModuleNameFromPath(path)));
    Set(&event, "psp", HexOut(psp, 4, true));
    Set(&event, "load_seg", HexOut(load_seg, 4, true));
    Set(&event, "reloc", HexOut(reloc, 4, true));
    Set(&event, "paragraphs", HexOut(paragraphs, 4, true));
    if (!overlay) {
        Set(&event, "rel_base", HexOut(com ? psp : load_seg, 4, true));
        Set(&event, "command_line", Str(CommandTailOf(psp)));
        Set(&event, "entry", Str(SegOff(csip)));
        Set(&event, "stack", Str(SegOff(sssp)));
    } else {
        Set(&event, "rel_base", HexOut(load_seg, 4, true));
    }
    WriteEvent("exec", event);
}

void RegisterEvents()
{
    Register("events.start", [](const JsonValue& params, JsonValue* result, Error* error) {
        Config cfg;
        if (!ParseConfig(params, &cfg, error))
            return false;
        std::string name = "events";
        if (Param(params, "name") != nullptr && (!ParamStr(params, "name", &name) || !PlainName(name)))
            return Fail(error, "bad_argument", "name must be a plain file name without path characters");
        if (g_active)
            StopEvents(false);
        g_path = OutputPath(name + ".jsonl");
        g_file.open(g_path, std::ios::binary | std::ios::trunc);
        if (!g_file)
            return Fail(error, "internal", "cannot open " + g_path);
        g_cfg = cfg;
        g_active = true;
        g_limit_hit = false;
        g_bytes = 0;
        g_count = 0;
        g_unflushed = 0;
        g_ring.clear();
        ++g_generation;
        UpdateHookActive();
        *result = StatusJson();
        return true;
    });
    Register("events.stop", [](const JsonValue&, JsonValue* result, Error*) {
        StopEvents(false);
        *result = StatusJson();
        return true;
    });
    Register("events.status", [](const JsonValue&, JsonValue* result, Error*) {
        *result = StatusJson();
        return true;
    });
    Register("events.read", [](const JsonValue& params, JsonValue* result, Error* error) {
        uint64_t since = 0;
        uint64_t max = 50;
        if (Param(params, "since_nr") != nullptr && !ParamU64(params, "since_nr", &since))
            return Fail(error, "bad_argument", "since_nr must be a number");
        if (Param(params, "max") != nullptr && (!ParamU64(params, "max", &max) || max == 0 || max > 500))
            return Fail(error, "bad_argument", "max must be between 1 and 500");
        if (g_active) {
            g_file.flush();
            g_unflushed = 0;
        }
        JsonValue list = JsonValue::Array();
        uint64_t oldest = 0;
        for (const JsonValue& event : g_ring) {
            const uint64_t nr = std::strtoull(event.Find("nr")->text.c_str(), nullptr, 10);
            if (oldest == 0)
                oldest = nr;
            if (nr > since && list.array.size() < max)
                list.array.push_back(event);
        }
        Set(result, "events", list);
        Set(result, "last_nr", Num(g_nr));
        Set(result, "oldest_in_buffer", Num(oldest));
        Set(result, "missed", Bool(oldest != 0 && since + 1 < oldest));
        Set(result, "active", Bool(g_active));
        Set(result, "file", Str(g_path));
        return true;
    });
}

} // namespace dmcp

void DMCP_OnExit(uint16_t psp, bool tsr, uint8_t exit_code)
{
    using namespace dmcp;
    dmcp::IntsOnExit(psp); // even with exit events off: the ints of the ended program never return
    dmcp::LeOnExit(psp);
    dmcp::CallsOnExit(psp);
    dmcp::FuncOnExit(psp);
    dmcp::FileIoOnExit(psp);
    dmcp::BatchOnExit(psp, exit_code); // run_batch: errorlevel of the last program (survives EXIT)
    if (!g_active || !g_cfg.flags.exit)
        return;
    JsonValue event = JsonValue::Object();
    Set(&event, "psp", HexOut(psp, 4, true));
    Set(&event, "exit_code", Num(exit_code));
    Set(&event, "kind", Str(tsr ? "tsr" : "normal"));
    for (auto it = Modules().rbegin(); it != Modules().rend(); ++it) {
        if (it->has_load && it->kind != "overlay" && it->psp == psp) {
            Set(&event, "module", Str(it->name));
            break;
        }
    }
    WriteEvent("exit", event);
}

#endif // C_DMCP
