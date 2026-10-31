// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "dmcp_modules.h"

#include <algorithm>
#include <cstdio>

namespace dmcp {
namespace {

uint32_t SymbolKey(const Module& m, const Symbol& s)
{
    return m.address_space == "rel16" ? (static_cast<uint32_t>(s.rel_seg) << 4) + s.addr : s.addr;
}

bool ParseSymbol(const JsonValue& entry, const std::string& space, size_t index, Symbol* out, Error* error)
{
    const std::string where = "symbol " + std::to_string(index);
    std::string addr;
    if (entry.type != JsonType::Object || !ParamStr(entry, "name", &out->name) || out->name.empty() ||
        !ParamStr(entry, "addr", &addr))
        return Fail(error, "bad_argument", where + " needs name and addr");
    if (out->name.find_first_of(":!") != std::string::npos)
        return Fail(error, "bad_argument", where + ": name must not contain ':' or '!'");
    if (space == "va32") {
        if (!ParseHex(addr, &out->addr))
            return Fail(error, "bad_argument", where + ": addr must be a hex va (0x...)");
    } else {
        const size_t colon = addr.find(':');
        uint32_t seg = 0;
        if (colon == std::string::npos || !ParseHex(addr.substr(0, colon), &seg) ||
            !ParseHex(addr.substr(colon + 1), &out->addr) || seg > 0xFFFFu || out->addr > 0xFFFFu)
            return Fail(error, "bad_argument", where + ": addr must be SSSS:OOOO for rel16");
        out->rel_seg = static_cast<uint16_t>(seg);
    }
    ParamStr(entry, "kind", &out->kind);
    if (Param(entry, "size") != nullptr && !HexField(entry, "size", &out->size))
        return Fail(error, "bad_argument", where + ": size must be a number");
    return true;
}

Address SymbolAddress(const Module& m, const Symbol& s, Error* error, bool* ok)
{
    char text[96];
    if (m.address_space == "rel16")
        snprintf(text, sizeof(text), "rel:%s:%04X:%04X", m.name.c_str(), static_cast<unsigned>(s.rel_seg),
                 static_cast<unsigned>(s.addr));
    else
        snprintf(text, sizeof(text), "va:%s:%X", m.name.c_str(), static_cast<unsigned>(s.addr));
    Address a;
    *ok = ParseModuleAddress(text, &a, error);
    return a;
}

} // namespace

void SetSymbols(Module& module, const std::string& address_space, std::vector<Symbol> symbols)
{
    module.address_space = address_space;
    module.symbols = symbols;
    std::stable_sort(module.symbols.begin(), module.symbols.end(), [&module](const Symbol& a, const Symbol& b) {
        return SymbolKey(module, a) < SymbolKey(module, b);
    });
}

std::string SymbolAt(const Module& m, uint32_t key)
{
    if (m.symbols.empty())
        return "";
    auto it = std::upper_bound(m.symbols.begin(), m.symbols.end(), key,
                               [&m](uint32_t k, const Symbol& s) { return k < SymbolKey(m, s); });
    if (it == m.symbols.begin())
        return "";
    --it;
    uint32_t delta = key - SymbolKey(m, *it);
    if (delta == 0)
        return it->name;
    // A size-less label right before the address must not hide the enclosing function: look back up to
    // 64 symbols for the first one that has a size and covers the address.
    for (int depth = 0; (it->size == 0 || delta >= it->size) && depth < 64; ++depth) {
        if (it == m.symbols.begin())
            return "";
        --it;
        delta = key - SymbolKey(m, *it);
    }
    if (it->size == 0 || delta >= it->size)
        return "";
    if (delta == 0)
        return it->name;
    char suffix[24];
    snprintf(suffix, sizeof(suffix), "+0x%X", static_cast<unsigned>(delta));
    return it->name + suffix;
}

bool ResolveSymbol(const std::string& text, Address* out, Error* error, bool* found)
{
    *found = false;
    std::string module;
    std::string name = text;
    const size_t bang = text.find('!');
    if (bang != std::string::npos) {
        module = Upper(text.substr(0, bang));
        name = text.substr(bang + 1);
    }
    const Module* hit = nullptr;
    const Symbol* symbol = nullptr;
    for (const Module& m : Modules()) {
        if (!module.empty() && m.name != module)
            continue;
        for (const Symbol& s : m.symbols) {
            if (s.name != name)
                continue;
            if (hit != nullptr) {
                *found = true;
                return Fail(error, "bad_argument", "symbol " + name + " exists in " + hit->name + " and " + m.name +
                                                       " (use MODULE!name)");
            }
            hit = &m;
            symbol = &s;
            break;
        }
    }
    if (hit == nullptr)
        return false;
    *found = true;
    bool ok = false;
    const Address a = SymbolAddress(*hit, *symbol, error, &ok);
    if (ok)
        *out = a;
    return ok;
}

void RegisterSymbols()
{
    Register("symbols.set", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string module, space;
        if (!ParamStr(params, "module", &module) || module.empty())
            return Fail(error, "bad_argument", "module is required");
        if (!ParamStr(params, "address_space", &space) || (space != "va32" && space != "rel16"))
            return Fail(error, "bad_argument", "address_space must be va32 or rel16");
        const JsonValue* list = Param(params, "symbols");
        if (list == nullptr || list->type != JsonType::Array)
            return Fail(error, "bad_argument", "symbols must be a list");
        std::vector<Symbol> symbols;
        for (size_t i = 0; i < list->array.size(); ++i) {
            Symbol s;
            if (!ParseSymbol(list->array[i], space, i, &s, error))
                return false;
            symbols.push_back(s);
        }
        JsonValue notes = JsonValue::Array();
        std::string mapping = "no";
        bool applied = false;
        uint32_t base = 0;
        const JsonValue* segs = Param(params, "segments");
        std::vector<ModuleSegment> segments;
        Origin origin = Origin::Set;
        for (const char* field : {"base_linear", "base_linear_loaded"}) {
            uint32_t unused = 0;
            if (Param(params, field) != nullptr && !HexField(params, field, &unused))
                return Fail(error, "bad_argument", std::string(field) + " is not a readable number (hex string)");
        }
        if (segs != nullptr) {
            uint32_t fallback = 0;
            const bool has_fallback =
                    HexField(params, "base_linear", &fallback) || HexField(params, "base_linear_loaded", &fallback);
            if (!ParseSegments(*segs, &segments, error, has_fallback ? &fallback : nullptr))
                return false;
            origin = Origin::SymbolsSegments;
        } else if (HexField(params, "base_linear", &base) || HexField(params, "base_linear_loaded", &base)) {
            ModuleSegment all;
            all.name = "all";
            all.va_start = 0;
            all.va_end = 0xFFFFFFFFu - base; // no wrap: linear addresses below the base stay unmapped
            all.base_linear = base;
            segments.push_back(all);
            origin = Param(params, "base_linear") != nullptr ? Origin::SymbolsBase : Origin::Set;
        }
        if (!segments.empty()) {
            if (!SetSegments(module, segments, origin, &applied, error))
                return false;
            if (applied)
                mapping = OriginName(origin);
            else
                notes.array.push_back(Str("a stronger va mapping exists; the mapping of this file was ignored"));
        }
        Module& m = GetOrCreateModule(Upper(module));
        SetSymbols(m, space, symbols);
        if (space == "va32" && m.segments.empty())
            notes.array.push_back(Str("no va mapping: names resolve after modules.set_segments"));
        Set(result, "module", Str(m.name));
        Set(result, "address_space", Str(space));
        Set(result, "count", Num(m.symbols.size()));
        Set(result, "mapping", Str(applied ? mapping : (m.segments.empty() ? "no" : OriginName(m.segments.front().origin))));
        Set(result, "hints", notes);
        return true;
    });
    Register("symbols.lookup", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string text;
        if (!ParamStr(params, "name", &text) && !ParamStr(params, "address", &text))
            return Fail(error, "bad_argument", "pass name or address");
        Address a;
        if (!ParseAddress(text, &a, error))
            return false;
        Set(result, "address", AddressJson(a));
        return true;
    });
    Register("symbols.clear", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string module;
        if (!ParamStr(params, "module", &module))
            return Fail(error, "bad_argument", "module is required");
        Module* m = FindModule(Upper(module));
        if (m != nullptr) {
            m->symbols.clear();
            m->address_space.clear();
        }
        Set(result, "module", Str(Upper(module)));
        return true;
    });
}

} // namespace dmcp

#endif // C_DMCP
