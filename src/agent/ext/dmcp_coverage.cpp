// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "cpu.h"
#include "regs.h"
#include "dmcp_addr.h"
#include "dmcp_modules.h"
#include "dmcp_record.h"

#include <algorithm>
#include <map>
#include <set>
#include <tuple>
#include <unordered_map>
#include <vector>

namespace dmcp {
namespace {

const uint32_t kBlockSize = 65536;
const size_t kBitmapBytes = kBlockSize / 8;

struct Context {
    uint16_t sel = 0;
    uint32_t base = 0;
    uint8_t bits = 16;
    uint8_t mode = 0; // 0 real, 1 v86, 2 pm
    bool operator<(const Context& o) const
    {
        return std::tie(mode, sel, base, bits) < std::tie(o.mode, o.sel, o.base, o.bits);
    }
};

struct Block {
    std::vector<uint8_t> bits = std::vector<uint8_t>(kBitmapBytes, 0);
    std::set<Context> contexts;
};

std::map<uint32_t, Block> g_blocks; // ordered: the file needs ascending blocks
bool g_active = false;
bool g_limit_hit = false;
uint64_t g_max_blocks = 4096;
Block* g_last = nullptr;
uint32_t g_last_start = 0;
bool g_ctx_valid = false;
Context g_ctx;
bool g_hits_on = false;
std::unordered_map<uint32_t, uint64_t> g_hits;
const size_t kMaxHits = 4000000;
bool g_hits_limit = false;

void Stop(bool limit)
{
    g_active = false;
    g_limit_hit = g_limit_hit || limit;
    g_last = nullptr;
    FlowResetIfIdle();
    UpdateHookActive();
}

uint64_t PopCount(const std::vector<uint8_t>& bits)
{
    uint64_t count = 0;
    for (uint8_t b : bits)
        for (uint8_t v = b; v != 0; v &= static_cast<uint8_t>(v - 1))
            ++count;
    return count;
}

JsonValue ContextJson(uint32_t block_start, const Block& block, const Context& c)
{
    JsonValue k = JsonValue::Object();
    if (c.mode == 2) {
        Set(&k, "sel", HexOut(c.sel, 4, true));
        Set(&k, "base_linear", HexOut(c.base, 8, true));
        Set(&k, "bits", Num(c.bits));
        Set(&k, "mode", Str("pm"));
        // first executed byte of the block decides the module (a block may start before the module)
        for (size_t i = 0; i < kBitmapBytes; ++i) {
            if (block.bits[i] == 0)
                continue;
            uint32_t bit = 0;
            while ((block.bits[i] & (1u << bit)) == 0)
                ++bit;
            const uint32_t first = block_start + static_cast<uint32_t>(i * 8 + bit);
            uint32_t va = 0;
            const Module* m = ModuleForLinear(first, &va);
            if (m != nullptr) {
                Set(&k, "module", Str(m->name));
                Set(&k, "va_start", HexOut(va - (first - block_start), 8, true)); // (linear_start - base) mod 2^32
            }
            break;
        }
        return k;
    }
    Set(&k, "seg", HexOut(c.sel, 4, true));
    Set(&k, "mode", Str(c.mode == 1 ? "v86" : "real"));
    Set(&k, "bits", Num(16));
    const Module* m = ModuleForRealSeg(c.sel);
    if (m != nullptr) {
        Set(&k, "module", Str(m->name));
        if (c.sel >= m->rel_base)
            Set(&k, "rel_seg", HexOut(static_cast<uint32_t>(c.sel - m->rel_base), 4, true));
    }
    return k;
}

bool Save(const std::string& name, JsonValue* result, Error* error)
{
    std::vector<uint8_t> bin;
    auto put32 = [&bin](uint32_t v) {
        for (int i = 0; i < 4; ++i)
            bin.push_back(static_cast<uint8_t>(v >> (8 * i)));
    };
    std::vector<const std::pair<const uint32_t, Block>*> used;
    uint64_t instructions = 0;
    for (const auto& entry : g_blocks) {
        const uint64_t count = PopCount(entry.second.bits);
        if (count != 0) {
            used.push_back(&entry);
            instructions += count;
        }
    }
    bin.push_back('D');
    bin.push_back('M');
    bin.push_back('C');
    bin.push_back('V');
    put32(1);
    put32(kBlockSize);
    put32(static_cast<uint32_t>(used.size()));
    JsonValue blocks = JsonValue::Array();
    for (const auto* entry : used) {
        put32(entry->first);
        bin.insert(bin.end(), entry->second.bits.begin(), entry->second.bits.end());
        JsonValue block = JsonValue::Object();
        Set(&block, "linear_start", HexOut(entry->first, 8, true));
        JsonValue contexts = JsonValue::Array();
        for (const Context& c : entry->second.contexts)
            contexts.array.push_back(ContextJson(entry->first, entry->second, c));
        Set(&block, "contexts", contexts);
        blocks.array.push_back(block);
    }
    std::string bin_path;
    if (!WriteOutputFile(name + ".bin", bin.data(), bin.size(), &bin_path, error))
        return false;
    JsonValue meta = JsonValue::Object();
    Set(&meta, "format", Str("dosbox-lab-coverage/1"));
    Set(&meta, "bin", Str(name + ".bin"));
    Set(&meta, "block_size", Num(kBlockSize));
    Set(&meta, "blocks", blocks);
    const std::string text = dosbox_agent::AGENT_SerializeJson(meta);
    std::string json_path;
    if (!WriteOutputFile(name + ".json", text.data(), text.size(), &json_path, error))
        return false;
    if (g_hits_on || !g_hits.empty()) {
        std::vector<std::pair<uint32_t, uint64_t>> sorted(g_hits.begin(), g_hits.end());
        std::sort(sorted.begin(), sorted.end());
        std::string lines;
        for (const auto& hit : sorted) {
            JsonValue line = JsonValue::Object();
            Set(&line, "linear", HexOut(hit.first, 8, true));
            Set(&line, "hits", Num(hit.second));
            lines += dosbox_agent::AGENT_SerializeJson(line) + "\n";
        }
        std::string hits_path;
        if (!WriteOutputFile(name + "_hits.jsonl", lines.data(), lines.size(), &hits_path, error))
            return false;
        Set(result, "hits_path", Str(hits_path));
    }
    Set(result, "path", Str(bin_path));
    Set(result, "json_path", Str(json_path));
    Set(result, "blocks", Num(used.size()));
    Set(result, "instructions", Num(instructions));
    return true;
}

JsonValue StatusJson()
{
    JsonValue r = JsonValue::Object();
    Set(&r, "active", Bool(g_active));
    Set(&r, "hits", Bool(g_hits_on));
    Set(&r, "blocks", Num(g_blocks.size()));
    Set(&r, "max_blocks", Num(g_max_blocks));
    Set(&r, "limit_reached_flag", Bool(g_limit_hit));
    return r;
}

} // namespace

bool CoverageActive() { return g_active; }
bool CoverageHitsOn() { return g_active && g_hits_on; }

void CoverageHit(uint32_t linear)
{
    auto it = g_hits.find(linear);
    if (it != g_hits.end()) {
        ++it->second;
        return;
    }
    if (g_hits.size() >= kMaxHits) {
        if (!g_hits_limit)
            ReportLimit("coverage_hits", "max " + std::to_string(kMaxHits) + " block starts");
        g_hits_limit = true;
        return;
    }
    g_hits.emplace(linear, 1);
}

void CoverageOnInstruction(uint32_t linear)
{
    const uint32_t start = linear & ~(kBlockSize - 1);
    if (g_last == nullptr || start != g_last_start) {
        auto it = g_blocks.find(start);
        if (it == g_blocks.end()) {
            if (g_blocks.size() >= g_max_blocks) {
                Stop(true);
                ReportLimit("coverage", "max_blocks " + std::to_string(g_max_blocks));
                return;
            }
            it = g_blocks.emplace(start, Block()).first;
        }
        g_last = &it->second;
        g_last_start = start;
        g_ctx_valid = false;
    }
    g_last->bits[(linear & (kBlockSize - 1)) >> 3] |= static_cast<uint8_t>(1u << (linear & 7u));
    const uint8_t mode = !cpu.pmode ? 0 : (GETFLAG(VM) ? 1 : 2);
    const uint16_t sel = static_cast<uint16_t>(SegValue(cs));
    const uint32_t base = static_cast<uint32_t>(SegPhys(cs));
    if (!g_ctx_valid || g_ctx.sel != sel || g_ctx.base != base || g_ctx.mode != mode) {
        g_ctx.sel = sel;
        g_ctx.base = base;
        g_ctx.bits = cpu.code.big ? 32 : 16;
        g_ctx.mode = mode;
        g_last->contexts.insert(g_ctx);
        g_ctx_valid = true;
    }
}

void RegisterCoverage()
{
    Register("coverage.start", [](const JsonValue& params, JsonValue* result, Error* error) {
        uint64_t max_blocks = 4096;
        bool keep = false;
        if (Param(params, "max_blocks") != nullptr &&
            (!ParamU64(params, "max_blocks", &max_blocks) || max_blocks == 0 || max_blocks > 65536))
            return Fail(error, "bad_argument", "max_blocks must be between 1 and 65536");
        if (Param(params, "resume") != nullptr && !ParamBool(params, "resume", &keep))
            return Fail(error, "bad_argument", "resume must be true or false");
        if (!keep)
            g_blocks.clear();
        bool hits = false;
        if (Param(params, "hits") != nullptr && !ParamBool(params, "hits", &hits))
            return Fail(error, "bad_argument", "hits must be true or false");
        FlowResetIfIdle(); // a pending branch from an earlier hits-only run must not resolve into this one
        g_hits_on = hits;
        if (!keep) {
            g_hits.clear();
            g_hits_limit = false;
        }
        g_max_blocks = max_blocks;
        g_active = true;
        g_limit_hit = false;
        g_last = nullptr;
        g_ctx_valid = false;
        UpdateHookActive();
        *result = StatusJson();
        return true;
    });
    Register("coverage.stop", [](const JsonValue&, JsonValue* result, Error*) {
        Stop(false);
        *result = StatusJson();
        return true;
    });
    Register("coverage.status", [](const JsonValue&, JsonValue* result, Error*) {
        *result = StatusJson();
        return true;
    });
    Register("coverage.save", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string name = "coverage";
        if (Param(params, "name") != nullptr && !ParamStr(params, "name", &name))
            return Fail(error, "bad_argument", "name must be a string");
        return Save(name, result, error);
    });
}

} // namespace dmcp

#endif // C_DMCP
