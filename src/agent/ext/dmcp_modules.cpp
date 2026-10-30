#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "dos_inc.h"
#include "agent/dmcp.h"
#include "dmcp_modules.h"
#include "dmcp_record.h"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstdlib>

namespace dmcp {
namespace {

std::vector<Module> g_modules;
std::string g_target; // module started last with 4B00h

std::vector<std::string> Split(const std::string& text, char separator)
{
    std::vector<std::string> parts;
    size_t start = 0;
    while (true) {
        const size_t next = text.find(separator, start);
        parts.push_back(text.substr(start, next == std::string::npos ? std::string::npos : next - start));
        if (next == std::string::npos)
            return parts;
        start = next + 1;
    }
}

JsonValue SegmentJson(const ModuleSegment& s)
{
    JsonValue o = JsonValue::Object();
    Set(&o, "name", Str(s.name));
    Set(&o, "va_start", Hex(s.va_start, 8));
    Set(&o, "va_ende", Hex(s.va_end, 8));
    Set(&o, "basis_linear", Hex(s.basis_linear, 8));
    Set(&o, "herkunft", Str(HerkunftName(s.herkunft)));
    return o;
}

JsonValue ModuleJson(const Module& m)
{
    JsonValue o = JsonValue::Object();
    Set(&o, "modul", Str(m.name));
    if (!m.datei.empty())
        Set(&o, "datei", Str(m.datei));
    if (!m.art.empty())
        Set(&o, "art", Str(m.art));
    if (m.has_load) {
        Set(&o, "psp", Hex(m.psp, 4));
        Set(&o, "load_seg", Hex(m.load_seg, 4));
        Set(&o, "rel_basis", Hex(m.rel_basis, 4));
        Set(&o, "paragraphen", Hex(m.paragraphs, 4));
    }
    JsonValue segments = JsonValue::Array();
    for (const ModuleSegment& s : m.segments)
        segments.array.push_back(SegmentJson(s));
    Set(&o, "segmente", segments);
    if (!m.adressraum.empty())
        Set(&o, "adressraum", Str(m.adressraum));
    Set(&o, "symbole", Num(m.symbols.size()));
    Set(&o, "ziel", Bool(!g_target.empty() && m.name == g_target));
    Set(&o, "exec_anzahl", Num(m.exec_count));
    if (m.le.vorhanden)
        Set(&o, "le", LeJson(m.le));
    return o;
}

Address LinearAddress(uint32_t linear)
{
    Address a;
    a.modus = "linear";
    a.has_linear = true;
    a.linear = linear;
    ResolvePhys(&a);
    return a;
}

} // namespace

std::vector<Module>& Modules() { return g_modules; }

Module* FindModule(const std::string& name)
{
    for (Module& m : g_modules)
        if (m.name == name)
            return &m;
    return nullptr;
}

Module& GetOrCreateModule(const std::string& name)
{
    Module* found = FindModule(name);
    if (found != nullptr)
        return *found;
    Module m;
    m.name = name;
    g_modules.push_back(m);
    return g_modules.back();
}

std::string Upper(std::string text)
{
    for (char& c : text)
        c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    return text;
}

std::string ModuleNameFromPath(const std::string& dos_path)
{
    const size_t cut = dos_path.find_last_of(":\\/");
    return Upper(cut == std::string::npos ? dos_path : dos_path.substr(cut + 1));
}

bool ParseHex(const std::string& text, uint32_t* out)
{
    std::string digits = text;
    if (digits.size() > 2 && digits[0] == '0' && (digits[1] == 'x' || digits[1] == 'X'))
        digits = digits.substr(2);
    if (digits.empty() || digits.size() > 8)
        return false;
    for (char c : digits)
        if (!std::isxdigit(static_cast<unsigned char>(c)))
            return false;
    *out = static_cast<uint32_t>(std::strtoul(digits.c_str(), nullptr, 16));
    return true;
}

const char* HerkunftName(Herkunft herkunft)
{
    switch (herkunft) {
    case Herkunft::SymboleSegmente: return "symbole_segmente";
    case Herkunft::SymboleBasis: return "symbole_basis";
    case Herkunft::Gesetzt: return "gesetzt";
    default: return "abgeleitet";
    }
}

bool ParseSegments(const JsonValue& list, std::vector<ModuleSegment>* out, Error* error, const uint32_t* default_basis)
{
    if (list.type != JsonType::Array)
        return Fail(error, "bad_argument", "segmente must be a list");
    out->clear();
    for (size_t i = 0; i < list.array.size(); ++i) {
        const JsonValue& entry = list.array[i];
        ModuleSegment s;
        bool has_basis = entry.type == JsonType::Object && HexField(entry, "basis_linear", &s.basis_linear);
        if (!has_basis && entry.type == JsonType::Object && Param(entry, "basis_linear") != nullptr)
            return Fail(error, "bad_argument",
                        "segment " + std::to_string(i) + ": basis_linear is not a readable number (hex string)");
        if (!has_basis && entry.type == JsonType::Object && default_basis != nullptr &&
            Param(entry, "basis_linear") == nullptr) {
            s.basis_linear = *default_basis;
            has_basis = true;
        }
        if (entry.type != JsonType::Object || !HexField(entry, "va_start", &s.va_start) ||
            !HexField(entry, "va_ende", &s.va_end) || !has_basis)
            return Fail(error, "bad_argument",
                        "segment " + std::to_string(i) + " needs va_start, va_ende and basis_linear (hex strings)");
        if (!ParamStr(entry, "name", &s.name) || s.name.empty())
            s.name = "seg" + std::to_string(i);
        out->push_back(s);
    }
    return true;
}

bool SetSegments(const std::string& modul, std::vector<ModuleSegment> segments, Herkunft herkunft, bool* applied,
                 Error* error)
{
    std::sort(segments.begin(), segments.end(),
              [](const ModuleSegment& a, const ModuleSegment& b) { return a.va_start < b.va_start; });
    for (size_t i = 0; i < segments.size(); ++i) {
        if (segments[i].va_end < segments[i].va_start)
            return Fail(error, "bad_argument", "segment " + segments[i].name + ": va_ende is below va_start");
        if (i > 0 && segments[i].va_start <= segments[i - 1].va_end)
            return Fail(error, "bad_argument", "segments " + segments[i - 1].name + " and " + segments[i].name +
                                                   " overlap (va: addresses would be ambiguous)");
        segments[i].herkunft = herkunft;
    }
    Module& m = GetOrCreateModule(Upper(modul));
    if (!m.segments.empty() && static_cast<int>(m.segments.front().herkunft) < static_cast<int>(herkunft)) {
        *applied = false; // a stronger mapping exists (spec rule 3)
        return true;
    }
    m.segments = segments;
    *applied = true;
    return true;
}

const Module* ModuleForLinear(uint32_t linear, uint32_t* va)
{
    for (auto it = g_modules.rbegin(); it != g_modules.rend(); ++it) {
        for (const ModuleSegment& s : it->segments) {
            const uint32_t candidate = linear - s.basis_linear;
            if (candidate >= s.va_start && candidate <= s.va_end) {
                *va = candidate;
                return &*it;
            }
        }
    }
    return nullptr;
}

const Module* ModuleForRealSeg(uint16_t seg)
{
    for (auto it = g_modules.rbegin(); it != g_modules.rend(); ++it) {
        if (!it->has_load)
            continue;
        const uint32_t start = it->art == "overlay" ? it->load_seg : it->psp;
        if (seg >= start && seg < start + it->paragraphs)
            return &*it;
    }
    return nullptr;
}

const Module* TargetModule()
{
    return g_target.empty() ? nullptr : FindModule(g_target);
}

void AnnotateAddress(const Address& a, JsonValue* object, bool file_format)
{
    if (a.has_selector && (a.modus == "real" || a.modus == "v86")) {
        const Module* m = ModuleForRealSeg(a.selector);
        if (m == nullptr)
            return;
        Set(object, "modul", Str(m->name));
        if (a.selector >= m->rel_basis && a.offset <= 0xFFFFu) {
            char rel[16];
            snprintf(rel, sizeof(rel), "%04X:%04X", static_cast<unsigned>(a.selector - m->rel_basis),
                          static_cast<unsigned>(a.offset));
            Set(object, "rel", Str(rel));
            if (m->adressraum == "rel16") {
                const std::string symbol =
                        SymbolAt(*m, (static_cast<uint32_t>(a.selector - m->rel_basis) << 4) + a.offset);
                if (!symbol.empty())
                    Set(object, "symbol", Str(symbol));
            }
        }
        return;
    }
    if (!a.has_linear)
        return;
    uint32_t va = 0;
    const Module* m = ModuleForLinear(a.linear, &va);
    if (m == nullptr)
        return;
    Set(object, "modul", Str(m->name));
    Set(object, "va", HexOut(va, 8, file_format));
    if (m->adressraum == "va32") {
        const std::string symbol = SymbolAt(*m, va);
        if (!symbol.empty())
            Set(object, "symbol", Str(symbol));
    }
}

bool ParseModuleAddress(const std::string& text, Address* out, Error* error)
{
    const std::vector<std::string> parts = Split(text, ':');
    if (parts[0] == "va") {
        if (parts.size() != 2 && parts.size() != 3)
            return Fail(error, "bad_argument", "va address must be va:<hex> or va:<MODUL>:<hex>");
        uint32_t va = 0;
        if (!ParseHex(parts.back(), &va))
            return Fail(error, "bad_argument", "bad va value in " + text);
        const Module* m = nullptr;
        if (parts.size() == 3) {
            m = FindModule(Upper(parts[1]));
            if (m == nullptr)
                return Fail(error, "bad_argument", "unknown module " + parts[1] + " (see modules.list)");
        } else {
            for (const Module& candidate : g_modules) {
                if (candidate.segments.empty())
                    continue;
                if (m != nullptr)
                    return Fail(error, "bad_argument", "several modules have a va mapping, use va:<MODUL>:<hex>");
                m = &candidate;
            }
            if (m == nullptr)
                return Fail(error, "bad_argument", "no module has a va mapping (modules.set_segments or symbols.load)");
        }
        for (const ModuleSegment& s : m->segments) {
            if (va >= s.va_start && va <= s.va_end) {
                *out = LinearAddress(s.basis_linear + va);
                return true;
            }
        }
        return Fail(error, "bad_address", "va " + parts.back() + " lies outside the segments of " + m->name);
    }
    if (parts.size() != 3 && parts.size() != 4)
        return Fail(error, "bad_argument", "rel address must be rel:<seg>:<off> or rel:<MODUL>:<seg>:<off>");
    const Module* m = parts.size() == 4 ? FindModule(Upper(parts[1])) : TargetModule();
    if (m == nullptr || !m->has_load)
        return Fail(error, "bad_argument", "no loaded module for " + text + " (start the program or name the module)");
    uint32_t seg = 0;
    uint32_t off = 0;
    if (!ParseHex(parts[parts.size() - 2], &seg) || !ParseHex(parts.back(), &off) || seg > 0xFFFFu || off > 0xFFFFu)
        return Fail(error, "bad_argument", "rel segment and offset must be hex values up to FFFF: " + text);
    Address a;
    a.modus = "real";
    a.has_selector = true;
    a.selector = static_cast<uint16_t>(m->rel_basis + seg);
    a.offset = off;
    a.base = static_cast<uint32_t>(a.selector) << 4;
    a.bits = 16;
    a.has_linear = true;
    a.linear = a.base + off;
    ResolvePhys(&a);
    *out = a;
    return true;
}

std::string CommandTailOf(uint16_t psp)
{
    // Physical reads (as dmcp_dos.cpp): real_readb goes through the TLB and would be logged by watches.
    const PhysPt base = static_cast<PhysPt>(psp) << 4;
    const uint8_t len = phys_readb(base + 0x80);
    std::string tail;
    for (uint8_t i = 0; i < len && i < 127; ++i) {
        const uint8_t c = phys_readb(base + 0x81 + i);
        tail.push_back(c >= 0x20 && c < 0x7F ? static_cast<char>(c) : '?');
    }
    return tail;
}

void ModulesOnExec(const std::string& dos_path, bool overlay, bool com, uint16_t psp, uint16_t load_seg,
                   uint32_t paragraphs, bool target, uint32_t csip, uint32_t sssp, const std::string& befehlszeile)
{
    const std::string name = ModuleNameFromPath(dos_path);
    GetOrCreateModule(name);
    // latest EXEC last, so ModuleForRealSeg/ModuleForLinear find it first
    auto it = std::find_if(g_modules.begin(), g_modules.end(), [&name](const Module& m) { return m.name == name; });
    std::rotate(it, it + 1, g_modules.end());
    Module& m = g_modules.back();
    m.datei = dos_path;
    m.art = overlay ? "overlay" : (com ? "com" : "exe");
    m.has_load = true;
    m.psp = psp;
    m.load_seg = load_seg;
    m.rel_basis = com ? psp : load_seg;
    m.paragraphs = paragraphs;
    ++m.exec_count;
    if (!overlay) {
        m.has_entry = true;
        m.csip = csip;
        m.sssp = sssp;
        m.befehlszeile = befehlszeile;
    }
    if (target)
        g_target = name;
}

void RegisterModules()
{
    Register("modules.list", [](const JsonValue&, JsonValue* result, Error*) {
        JsonValue list = JsonValue::Array();
        for (const Module& m : g_modules)
            list.array.push_back(ModuleJson(m));
        Set(result, "module", list);
        return true;
    });
    Register("modules.set_segments", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string modul;
        if (!ParamStr(params, "modul", &modul) || modul.empty())
            return Fail(error, "bad_argument", "modul is required");
        const JsonValue* list = Param(params, "segmente");
        if (list == nullptr)
            return Fail(error, "bad_argument", "segmente is required");
        std::vector<ModuleSegment> segments;
        if (!ParseSegments(*list, &segments, error))
            return false;
        bool applied = false;
        if (!SetSegments(modul, segments, Herkunft::Gesetzt, &applied, error))
            return false;
        const Module* m = FindModule(Upper(modul));
        Set(result, "modul", Str(m->name));
        Set(result, "angewendet", Bool(applied));
        if (!applied)
            Set(result, "hinweis", Str("the symbol file mapping of this module has priority (spec rule 3)"));
        Set(result, "modul_info", ModuleJson(*m));
        return true;
    });
    Register("modules.clear", [](const JsonValue& params, JsonValue* result, Error* error) {
        bool all = false;
        std::string modul;
        if (ParamBool(params, "alle", &all) && all) {
            g_modules.clear();
            g_target.clear();
        } else if (ParamStr(params, "modul", &modul)) {
            const std::string name = Upper(modul);
            g_modules.erase(std::remove_if(g_modules.begin(), g_modules.end(),
                                           [&name](const Module& m) { return m.name == name; }),
                            g_modules.end());
            if (g_target == name)
                g_target.clear();
        } else {
            return Fail(error, "bad_argument", "pass modul or alle:true");
        }
        Set(result, "anzahl", Num(g_modules.size()));
        return true;
    });
}

} // namespace dmcp

void DMCP_OnExec(const char* name, uint16_t flags, bool com, uint16_t psp, uint16_t load_seg, uint16_t reloc,
                 uint32_t paragraphs, uint32_t csip, uint32_t sssp)
{
    char canonical[DOS_PATHLENGTH + 1] = {0};
    std::string path = DOS_Canonicalize(name, canonical) ? std::string(canonical) : std::string(name);
    for (char& c : path) { // guest bytes must stay valid UTF-8 in JSON (same rule as ReadString)
        const unsigned char u = static_cast<unsigned char>(c);
        if (u < 0x20 || u >= 0x80)
            c = '?';
    }
    dmcp::ModulesOnExec(path, flags == 3, com, psp, load_seg, paragraphs, flags == 0, csip, sssp,
                       flags == 3 ? std::string() : dmcp::CommandTailOf(psp));
    dmcp::EventOnExec(path, flags, com, psp, load_seg, reloc, paragraphs, csip, sssp);
    dmcp::BatchOnExec(path, flags == 0);
    dmcp::LeOnExec(name, dmcp::ModuleNameFromPath(path), flags);
}

#endif // C_DMCP
