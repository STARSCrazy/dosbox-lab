// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "cpu.h"
#include "dmcp_addr.h"

#include <algorithm>
#include <cctype>
#include <string>
#include <vector>

namespace dmcp {
namespace {

const char kHexDigits[] = "0123456789ABCDEF";

std::string ToHex(const uint8_t* data, size_t len)
{
    std::string text;
    text.reserve(len * 2);
    for (size_t i = 0; i < len; ++i) {
        text.push_back(kHexDigits[data[i] >> 4]);
        text.push_back(kHexDigits[data[i] & 15]);
    }
    return text;
}

} // namespace

bool FromHex(const std::string& text, std::vector<uint8_t>* out)
{
    if (text.empty() || text.size() % 2 != 0)
        return false;
    out->clear();
    for (size_t i = 0; i < text.size(); i += 2) {
        if (!std::isxdigit(static_cast<unsigned char>(text[i])) ||
            !std::isxdigit(static_cast<unsigned char>(text[i + 1])))
            return false;
        out->push_back(static_cast<uint8_t>(std::stoul(text.substr(i, 2), nullptr, 16)));
    }
    return true;
}

namespace {

// Real mode: no program paging, and DOS extenders often sit here during DOS calls.
void AddRealModeHint(JsonValue* result)
{
    if (!cpu.pmode)
        Set(result, "hinweis", Str("Gast im Real Mode, Programm-Paging inaktiv"));
}

bool AddressParam(const JsonValue& params, Address* address, Error* error)
{
    std::string text;
    if (!ParamStr(params, "adresse", &text))
        return Fail(error, "bad_argument", "parameter adresse (string) is required");
    return ParseAddress(text, address, error);
}

bool LengthParam(const JsonValue& params, uint64_t max, uint64_t* length, Error* error)
{
    if (!ParamU64(params, "laenge", length) || *length == 0 || *length > max)
        return Fail(error, "bad_argument", "laenge must be between 1 and " + std::to_string(max));
    return true;
}

} // namespace

void RegisterMem()
{
    Register("mem.read", [](const JsonValue& params, JsonValue* result, Error* error) {
        Address address;
        uint64_t length = 0;
        if (!AddressParam(params, &address, error) || !LengthParam(params, 4096, &length, error))
            return false;
        std::vector<uint8_t> data(static_cast<size_t>(length));
        RangeList missing;
        ReadAddress(address, data.data(), data.size(), &missing);
        Set(result, "adresse", AddressJson(address));
        Set(result, "laenge", Num(length));
        Set(result, "hex", Str(ToHex(data.data(), data.size())));
        Set(result, "fehlend", RangesJson(missing));
        AddRealModeHint(result);
        return true;
    });
    Register("mem.write", [](const JsonValue& params, JsonValue* result, Error* error) {
        Address address;
        std::string hex;
        std::vector<uint8_t> data;
        if (!AddressParam(params, &address, error))
            return false;
        if (!ParamStr(params, "hex", &hex) || !FromHex(hex, &data) || data.size() > 65536)
            return Fail(error, "bad_argument", "hex must be an even-length hex string of at most 64 KiB");
        if (!WriteAddress(address, data.data(), data.size(), error))
            return false;
        Set(result, "adresse", AddressJson(address));
        Set(result, "laenge", Num(data.size()));
        return true;
    });
    Register("mem.dump", [](const JsonValue& params, JsonValue* result, Error* error) {
        Address address;
        uint64_t length = 0;
        std::string name;
        if (!AddressParam(params, &address, error) || !LengthParam(params, 64ull << 20, &length, error))
            return false;
        if (!ParamStr(params, "name", &name))
            return Fail(error, "bad_argument", "parameter name is required");
        std::vector<uint8_t> data(static_cast<size_t>(length));
        RangeList missing;
        ReadAddress(address, data.data(), data.size(), &missing);
        std::string bin_path;
        if (!WriteOutputFile(name + ".bin", data.data(), data.size(), &bin_path, error))
            return false;
        JsonValue meta = JsonValue::Object();
        Set(&meta, "format", Str("dosbox-lab-dump/1"));
        Set(&meta, "adresse", AddressJson(address));
        Set(&meta, "laenge", Num(length));
        Set(&meta, "fehlend", RangesJson(missing));
        const std::string meta_text = dosbox_agent::AGENT_SerializeJson(meta);
        std::string json_path;
        if (!WriteOutputFile(name + ".json", meta_text.data(), meta_text.size(), &json_path, error))
            return false;
        Set(result, "pfad", Str(bin_path));
        Set(result, "json_pfad", Str(json_path));
        Set(result, "laenge", Num(length));
        Set(result, "fehlend", RangesJson(missing));
        AddRealModeHint(result);
        return true;
    });
    Register("mem.search", [](const JsonValue& params, JsonValue* result, Error* error) {
        Address address;
        uint64_t length = 0;
        uint64_t max_hits = 100;
        if (!AddressParam(params, &address, error) || !LengthParam(params, 256ull << 20, &length, error))
            return false;
        std::vector<uint8_t> pattern;
        std::string text;
        if (ParamStr(params, "hex", &text)) {
            if (!FromHex(text, &pattern))
                return Fail(error, "bad_argument", "hex pattern is not valid hex");
        } else if (ParamStr(params, "text", &text) && !text.empty()) {
            pattern.assign(text.begin(), text.end());
        } else {
            return Fail(error, "bad_argument", "pass hex or text");
        }
        if (Param(params, "max_treffer") != nullptr && (!ParamU64(params, "max_treffer", &max_hits) || max_hits == 0))
            return Fail(error, "bad_argument", "max_treffer must be positive");
        JsonValue hits = JsonValue::Array();
        bool truncated = false;
        const size_t chunk = 1u << 20;
        std::vector<uint8_t> buffer;
        RangeList all_missing;
        for (uint64_t pos = 0; pos < length && !truncated; pos += chunk) {
            const size_t want = static_cast<size_t>(std::min<uint64_t>(chunk + pattern.size() - 1, length - pos));
            buffer.resize(want);
            RangeList missing;
            const Address chunk_address = Offset(address, static_cast<uint32_t>(pos));
            ReadAddress(chunk_address, buffer.data(), want, &missing);
            // Missing bytes read as 0 and must not produce hits; ranges are absolute (linear, or physical for p:).
            const uint32_t chunk_base = chunk_address.has_linear ? chunk_address.linear : chunk_address.phys;
            all_missing.insert(all_missing.end(), missing.begin(), missing.end());
            auto it = buffer.begin();
            while (true) {
                it = std::search(it, buffer.end(), pattern.begin(), pattern.end());
                if (it == buffer.end())
                    break;
                const size_t index = static_cast<size_t>(it - buffer.begin());
                if (index >= chunk)
                    break; // belongs to the next chunk
                // Chunk-relative comparison, safe against 32-bit wrap (a range ending at 2^32 is stored with end 0).
                const uint64_t hit_end = static_cast<uint64_t>(index) + pattern.size();
                uint64_t skip_to = 0;
                for (const auto& range : missing) {
                    const uint64_t rel = static_cast<uint32_t>(range.first - chunk_base);
                    const uint64_t len = static_cast<uint32_t>(range.second - range.first);
                    const uint64_t rel_end = rel + (len == 0 ? (1ull << 32) : len);
                    if (rel < hit_end && index < rel_end)
                        skip_to = (std::max)(skip_to, rel_end);
                }
                if (skip_to != 0) {
                    // Continue behind the missing range instead of matching byte by byte inside it.
                    it = buffer.begin() + static_cast<ptrdiff_t>((std::min<uint64_t>)(skip_to, buffer.size()));
                    continue;
                }
                if (hits.array.size() >= max_hits) {
                    truncated = true;
                    break;
                }
                hits.array.push_back(AddressJson(Offset(address, static_cast<uint32_t>(pos + index))));
                ++it;
            }
        }
        Set(result, "treffer", hits);
        Set(result, "abgeschnitten", Bool(truncated));
        if (!all_missing.empty()) {
            // Chunks overlap by pattern-1 bytes, so the same range can appear twice: sort and merge.
            std::sort(all_missing.begin(), all_missing.end());
            RangeList merged;
            for (const auto& range : all_missing) {
                if (!merged.empty() && range.first <= merged.back().second)
                    merged.back().second = (std::max)(merged.back().second, range.second);
                else
                    merged.push_back(range);
            }
            Set(result, "fehlend", RangesJson(merged));
            AddRealModeHint(result);
        }
        return true;
    });
}

} // namespace dmcp

#endif // C_DMCP
