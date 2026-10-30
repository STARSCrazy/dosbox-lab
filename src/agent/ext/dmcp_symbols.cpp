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
    return m.adressraum == "rel16" ? (static_cast<uint32_t>(s.rel_seg) << 4) + s.adr : s.adr;
}

bool ParseSymbol(const JsonValue& entry, const std::string& raum, size_t index, Symbol* out, Error* error)
{
    const std::string where = "symbol " + std::to_string(index);
    std::string adr;
    if (entry.type != JsonType::Object || !ParamStr(entry, "name", &out->name) || out->name.empty() ||
        !ParamStr(entry, "adr", &adr))
        return Fail(error, "bad_argument", where + " needs name and adr");
    if (out->name.find_first_of(":!") != std::string::npos)
        return Fail(error, "bad_argument", where + ": name must not contain ':' or '!'");
    if (raum == "va32") {
        if (!ParseHex(adr, &out->adr))
            return Fail(error, "bad_argument", where + ": adr must be a hex va (0x...)");
    } else {
        const size_t colon = adr.find(':');
        uint32_t seg = 0;
        if (colon == std::string::npos || !ParseHex(adr.substr(0, colon), &seg) ||
            !ParseHex(adr.substr(colon + 1), &out->adr) || seg > 0xFFFFu || out->adr > 0xFFFFu)
            return Fail(error, "bad_argument", where + ": adr must be SSSS:OOOO for rel16");
        out->rel_seg = static_cast<uint16_t>(seg);
    }
    ParamStr(entry, "art", &out->art);
    if (Param(entry, "groesse") != nullptr && !HexField(entry, "groesse", &out->size))
        return Fail(error, "bad_argument", where + ": groesse must be a number");
    return true;
}

Address SymbolAddress(const Module& m, const Symbol& s, Error* error, bool* ok)
{
    char text[96];
    if (m.adressraum == "rel16")
        snprintf(text, sizeof(text), "rel:%s:%04X:%04X", m.name.c_str(), static_cast<unsigned>(s.rel_seg),
                 static_cast<unsigned>(s.adr));
    else
        snprintf(text, sizeof(text), "va:%s:%X", m.name.c_str(), static_cast<unsigned>(s.adr));
    Address a;
    *ok = ParseModuleAddress(text, &a, error);
    return a;
}

} // namespace

void SetSymbols(Module& module, const std::string& adressraum, std::vector<Symbol> symbols)
{
    module.adressraum = adressraum;
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
    std::string modul;
    std::string name = text;
    const size_t bang = text.find('!');
    if (bang != std::string::npos) {
        modul = Upper(text.substr(0, bang));
        name = text.substr(bang + 1);
    }
    const Module* hit = nullptr;
    const Symbol* symbol = nullptr;
    for (const Module& m : Modules()) {
        if (!modul.empty() && m.name != modul)
            continue;
        for (const Symbol& s : m.symbols) {
            if (s.name != name)
                continue;
            if (hit != nullptr) {
                *found = true;
                return Fail(error, "bad_argument", "symbol " + name + " exists in " + hit->name + " and " + m.name +
                                                       " (use MODUL!name)");
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
        std::string modul, raum;
        if (!ParamStr(params, "modul", &modul) || modul.empty())
            return Fail(error, "bad_argument", "modul is required");
        if (!ParamStr(params, "adressraum", &raum) || (raum != "va32" && raum != "rel16"))
            return Fail(error, "bad_argument", "adressraum must be va32 or rel16");
        const JsonValue* list = Param(params, "symbole");
        if (list == nullptr || list->type != JsonType::Array)
            return Fail(error, "bad_argument", "symbole must be a list");
        std::vector<Symbol> symbols;
        for (size_t i = 0; i < list->array.size(); ++i) {
            Symbol s;
            if (!ParseSymbol(list->array[i], raum, i, &s, error))
                return false;
            symbols.push_back(s);
        }
        JsonValue notes = JsonValue::Array();
        std::string mapping = "keine";
        bool applied = false;
        uint32_t basis = 0;
        const JsonValue* segs = Param(params, "segmente");
        std::vector<ModuleSegment> segments;
        Herkunft herkunft = Herkunft::Gesetzt;
        for (const char* field : {"basis_linear", "basis_linear_geladen"}) {
            uint32_t unused = 0;
            if (Param(params, field) != nullptr && !HexField(params, field, &unused))
                return Fail(error, "bad_argument", std::string(field) + " is not a readable number (hex string)");
        }
        if (segs != nullptr) {
            uint32_t fallback = 0;
            const bool has_fallback =
                    HexField(params, "basis_linear", &fallback) || HexField(params, "basis_linear_geladen", &fallback);
            if (!ParseSegments(*segs, &segments, error, has_fallback ? &fallback : nullptr))
                return false;
            herkunft = Herkunft::SymboleSegmente;
        } else if (HexField(params, "basis_linear", &basis) || HexField(params, "basis_linear_geladen", &basis)) {
            ModuleSegment all;
            all.name = "alles";
            all.va_start = 0;
            all.va_end = 0xFFFFFFFFu - basis; // no wrap: linear addresses below the base stay unmapped
            all.basis_linear = basis;
            segments.push_back(all);
            herkunft = Param(params, "basis_linear") != nullptr ? Herkunft::SymboleBasis : Herkunft::Gesetzt;
        }
        if (!segments.empty()) {
            if (!SetSegments(modul, segments, herkunft, &applied, error))
                return false;
            if (applied)
                mapping = HerkunftName(herkunft);
            else
                notes.array.push_back(Str("a stronger va mapping exists; the mapping of this file was ignored"));
        }
        Module& m = GetOrCreateModule(Upper(modul));
        SetSymbols(m, raum, symbols);
        if (raum == "va32" && m.segments.empty())
            notes.array.push_back(Str("no va mapping: names resolve after modules.set_segments"));
        Set(result, "modul", Str(m.name));
        Set(result, "adressraum", Str(raum));
        Set(result, "anzahl", Num(m.symbols.size()));
        Set(result, "abbildung", Str(applied ? mapping : (m.segments.empty() ? "keine" : HerkunftName(m.segments.front().herkunft))));
        Set(result, "hinweise", notes);
        return true;
    });
    Register("symbols.lookup", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string text;
        if (!ParamStr(params, "name", &text) && !ParamStr(params, "adresse", &text))
            return Fail(error, "bad_argument", "pass name or adresse");
        Address a;
        if (!ParseAddress(text, &a, error))
            return false;
        Set(result, "adresse", AddressJson(a));
        return true;
    });
    Register("symbols.clear", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string modul;
        if (!ParamStr(params, "modul", &modul))
            return Fail(error, "bad_argument", "modul is required");
        Module* m = FindModule(Upper(modul));
        if (m != nullptr) {
            m->symbols.clear();
            m->adressraum.clear();
        }
        Set(result, "modul", Str(Upper(modul)));
        return true;
    });
}

} // namespace dmcp

#endif // C_DMCP
