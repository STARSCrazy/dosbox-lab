// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "cpu.h"
#include "mem.h"
#include "pic.h"
#include "regs.h"
#include "shell.h"
#include "dmcp_internal.h"
#include "dmcp_record.h"
#include "dmcp_state.h"
#include "dmcp_watch.h"
#include "unzip.h"

#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>

extern bool use_save_file;
extern std::string savefilename;
extern bool noremark_save_state;
extern bool force_load_state;
extern bool dos_kernel_disabled; // sdlmain.cpp: true until the DOS kernel is up (loading a state earlier crashes)

namespace dmcp {
namespace {

// Upstream SaveState::load/save swallow ZIP errors silently (half-restored state, truncated file), so check
// the archive ourselves: required entries present and every entry readable with a matching CRC.
bool VerifyStateZip(const std::string& path, std::string* why)
{
    unzFile zf = unzOpen64(path.c_str());
    if (zf == nullptr) {
        *why = "not a readable ZIP archive";
        return false;
    }
    bool ok = true;
    static const char* const kRequired[] = {"DOSBox-X_Version", "Program_Name", "Memory_Size", "Machine_Type"};
    for (const char* entry : kRequired) {
        if (unzLocateFile(zf, entry, 1) != UNZ_OK) {
            *why = std::string("entry ") + entry + " missing";
            ok = false;
            break;
        }
    }
    if (ok) {
        char buffer[65536];
        int rc = unzGoToFirstFile(zf);
        while (ok && rc == UNZ_OK) {
            if (unzOpenCurrentFile(zf) != UNZ_OK) {
                *why = "an entry cannot be opened";
                ok = false;
                break;
            }
            int n;
            while ((n = unzReadCurrentFile(zf, buffer, sizeof(buffer))) > 0) {
            }
            if (n < 0) {
                *why = "an entry cannot be read (truncated or corrupt)";
                ok = false;
            }
            if (unzCloseCurrentFile(zf) != UNZ_OK && ok) {
                *why = "CRC error in an entry";
                ok = false;
            }
            rc = unzGoToNextFile(zf);
        }
        if (ok && rc != UNZ_END_OF_LIST_OF_FILE) {
            *why = "central directory is damaged";
            ok = false;
        }
    }
    unzClose(zf);
    return ok;
}

// Page count from the archive's own "Memory_Size" entry (decimal text, savestates.cpp). Upstream copies the
// saved pages into MemBase without checking the size once force_load_state skips its confirmation box, so a
// mismatch must never reach SaveState::load (also with skip_check or a stale sidecar).
bool StateZipPages(const std::string& path, unsigned long long* pages)
{
    unzFile zf = unzOpen64(path.c_str());
    if (zf == nullptr)
        return false;
    bool ok = false;
    if (unzLocateFile(zf, "Memory_Size", 1) == UNZ_OK && unzOpenCurrentFile(zf) == UNZ_OK) {
        char buffer[32];
        const int n = unzReadCurrentFile(zf, buffer, sizeof(buffer) - 1);
        unzCloseCurrentFile(zf);
        if (n > 0) {
            buffer[n] = 0;
            char* end = nullptr;
            *pages = std::strtoull(buffer, &end, 10);
            ok = end != buffer && *end == 0;
        }
    }
    unzClose(zf);
    return ok;
}

bool Over1GB()
{
    return MEM_TotalPages() * 4096ull > 1024ull * 1024 * 1024;
}

// Upstream save/load with a file instead of a slot and without any dialog; restores the globals afterwards.
class UpstreamFileMode {
public:
    explicit UpstreamFileMode(const std::string& path)
        : use_(use_save_file), file_(savefilename), noremark_(noremark_save_state), force_(force_load_state)
    {
        use_save_file = true;
        savefilename = path;
        noremark_save_state = true; // no remark input box
        force_load_state = true;    // no version/program confirmation box
    }
    ~UpstreamFileMode()
    {
        use_save_file = use_;
        savefilename = file_;
        noremark_save_state = noremark_;
        force_load_state = force_;
    }

private:
    bool use_;
    std::string file_;
    bool noremark_;
    bool force_;
};

bool ReadFile(const std::string& path, std::string* out)
{
    std::ifstream in(path, std::ios::binary);
    if (!in)
        return false;
    std::ostringstream buffer;
    buffer << in.rdbuf();
    *out = buffer.str();
    return true;
}

bool ReadHeader(const std::string& path, std::string* out)
{
    std::ifstream in(path, std::ios::binary);
    if (!in)
        return false;
    char head[4] = {0, 0, 0, 0};
    in.read(head, sizeof(head));
    out->assign(head, static_cast<size_t>(in.gcount()));
    return true;
}

// Non-negative integer field of the parsed sidecar.
bool SidecarNumber(const JsonValue& json, const std::string& key, unsigned long long* out)
{
    uint64_t value = 0;
    if (json.type != JsonType::Object || Param(json, key) == nullptr || Param(json, key)->type != JsonType::Number ||
        !ParamU64(json, key, &value))
        return false;
    *out = value;
    return true;
}

std::string BaseWithout(const std::string& path, const std::string& ext)
{
    if (path.size() > ext.size() && path.compare(path.size() - ext.size(), ext.size(), ext) == 0)
        return path.substr(0, path.size() - ext.size());
    return path;
}

} // namespace

bool SaveStateFile(const std::string& name, const JsonValue* call, std::string* path_out, std::string* info_path,
                   uint64_t* size_out, Error* error)
{
    if (!PlainName(name))
        return Fail(error, "bad_argument", "name must be a plain file name without path characters");
    if (Over1GB())
        return Fail(error, "bad_argument", "savestates support at most 1 GB guest RAM");
    const std::string path = OutputPath(name + ".sav");
    std::remove(path.c_str());
    try {
        UpstreamFileMode mode(path);
        SaveState::instance().save(0);
    } catch (...) {
        return Fail(error, "internal", "savestate failed (see dosbox log)");
    }
    std::ifstream check(path, std::ios::binary | std::ios::ate);
    if (!check || check.tellg() <= 0)
        return Fail(error, "internal", "savestate file was not written (see dosbox log): " + path);
    const uint64_t size = static_cast<uint64_t>(check.tellg());
    check.close();
    std::string why;
    if (!VerifyStateZip(path, &why))
        return Fail(error, "internal", "savestate was not written correctly (" + why + "): " + path);
    JsonValue info = JsonValue::Object();
    Set(&info, "format", Str("dosbox-lab-state/1"));
    Set(&info, "memory_pages", Num(static_cast<uint64_t>(MEM_TotalPages())));
    Set(&info, "machine", Num(static_cast<uint64_t>(static_cast<int>(machine))));
    Set(&info, "version", Str(VERSION));
    Set(&info, "time_ms", NumFixed(PIC_FullIndex()));
    Set(&info, "cs", Hex(SegValue(cs), 4));
    Set(&info, "eip", Hex(reg_eip, 8));
    Set(&info, "pmode", Bool(cpu.pmode));
    if (call != nullptr)
        Set(&info, "call", *call);
    const std::string text = dosbox_agent::AGENT_SerializeJson(info) + "\n";
    if (!WriteOutputFile(name + ".json", text.data(), text.size(), info_path, error))
        return false;
    *path_out = path;
    *size_out = size;
    return true;
}

bool ReadStateSidecar(const std::string& path, JsonValue* sidecar)
{
    std::string text, perr;
    return ReadFile(BaseWithout(path, ".sav") + ".json", &text) && dosbox_agent::AGENT_ParseJson(text, sidecar, &perr) &&
           sidecar->type == JsonType::Object;
}

bool LoadStateFile(const std::string& path, bool unchecked, JsonValue* sidecar, Error* error)
{
    *sidecar = JsonValue();
    // Loading while the DOS shell is not up crashes the emulator: during the start, and also after BOOT of a guest
    // OS (the DOS kernel is off again then).
    if (dos_kernel_disabled || first_shell == nullptr || DOS_ShellGetPSP() == 0)
        return Fail(error, "not_ready",
                    "DOS shell is not up (emulator still starting, or a guest OS was booted); loading would crash");
    if (Over1GB())
        return Fail(error, "bad_argument", "savestates support at most 1 GB guest RAM");
    std::string data;
    if (!ReadHeader(path, &data))
        return Fail(error, "bad_argument", "savestate file not found: " + path);
    if (data.size() < 4 || data[0] != 'P' || data[1] != 'K')
        return Fail(error, "bad_argument", "not a DOSBox-X savestate (no ZIP header): " + path);
    {
        std::string why;
        if (!VerifyStateZip(path, &why))
            return Fail(error, "bad_argument", "damaged savestate (" + why + "): " + path);
        unsigned long long zip_pages = 0;
        if (!StateZipPages(path, &zip_pages))
            return Fail(error, "bad_argument", "memory: savestate has no readable Memory_Size entry: " + path);
        if (zip_pages != MEM_TotalPages())
            return Fail(error, "bad_argument", "memory: savestate has " + std::to_string(zip_pages) +
                                                   " pages, this instance " + std::to_string(MEM_TotalPages()));
    }
    std::string text;
    const std::string info_path = BaseWithout(path, ".sav") + ".json";
    if (ReadFile(info_path, &text)) {
        std::string perr;
        unsigned long long pages = 0, mach = 0;
        if (!dosbox_agent::AGENT_ParseJson(text, sidecar, &perr) || !SidecarNumber(*sidecar, "memory_pages", &pages) ||
            !SidecarNumber(*sidecar, "machine", &mach))
            return Fail(error, "bad_argument", "sidecar " + info_path + " is not a dosbox-lab-state/1 file");
        if (pages != MEM_TotalPages())
            return Fail(error, "bad_argument", "memory: savestate has " + std::to_string(pages) +
                                                   " pages, this instance " + std::to_string(MEM_TotalPages()));
        if (static_cast<int>(mach) != static_cast<int>(machine))
            return Fail(error, "bad_argument", "machine: savestate was made with another machine type");
    } else if (!unchecked) {
        return Fail(error, "bad_argument", "sidecar " + info_path +
                                               " missing; pass skip_check:true to load without the check");
    }
    const double before_ms = PIC_FullIndex();
    try {
        UpstreamFileMode mode(path);
        SaveState::instance().load(0);
    } catch (...) {
        return Fail(error, "internal", "loading the savestate failed (see dosbox log)");
    }
    // Stop condition and pending watch stop belong to the replaced state.
    DisarmForLoad();
    WatchDropPending();
    FlowOnStateLoad();
    CallsOnStateLoad();
    FileIoOnStateLoad();
    FuncOnStateLoad();
    IntsOnStateLoad();
    LeOnStateLoad();
    InputOnStateLoad(before_ms);
    return true;
}

void RegisterState()
{
    Register("state.save", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string name = "state";
        if (Param(params, "name") != nullptr && !ParamStr(params, "name", &name))
            return Fail(error, "bad_argument", "name must be a string");
        std::string path, info_path;
        uint64_t size = 0;
        if (!SaveStateFile(name, nullptr, &path, &info_path, &size, error))
            return false;
        Set(result, "path", Str(path));
        Set(result, "size", Num(size));
        Set(result, "info_path", Str(info_path));
        return true;
    });
    Register("state.load", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string name, path;
        bool unchecked = false;
        if (Param(params, "path") != nullptr) {
            if (!ParamStr(params, "path", &path) || path.empty())
                return Fail(error, "bad_argument", "path must be a non-empty string");
            // absolute or relative host path given by the caller
        } else if (Param(params, "name") == nullptr || ParamStr(params, "name", &name)) {
            if (name.empty())
                name = "state";
            if (!PlainName(name))
                return Fail(error, "bad_argument", "name must be a plain file name without path characters");
            path = OutputPath(name + ".sav");
        } else {
            return Fail(error, "bad_argument", "name or path must be a string");
        }
        if (Param(params, "skip_check") != nullptr && !ParamBool(params, "skip_check", &unchecked))
            return Fail(error, "bad_argument", "skip_check must be true or false");
        JsonValue sidecar;
        if (!LoadStateFile(path, unchecked, &sidecar, error))
            return false;
        Set(result, "path", Str(path));
        Set(result, "loaded", Bool(true));
        Set(result, "cs", Hex(SegValue(cs), 4));
        Set(result, "eip", Hex(reg_eip, 8));
        Set(result, "pmode", Bool(cpu.pmode));
        return true;
    });
}

} // namespace dmcp

#endif // C_DMCP
