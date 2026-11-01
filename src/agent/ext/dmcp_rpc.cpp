// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include "agent/dmcp.h"
#include "agent/agent_bridge.h"
#include "dmcp_internal.h"

#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cctype>
#include <cstring>
#include <fstream>
#include <map>
#include <memory>
#include <mutex>

namespace dmcp {
namespace {

struct Entry {
    Handler handler;
    bool on_emulation_thread;
};

std::map<std::string, Entry>& Table()
{
    static std::map<std::string, Entry> table;
    return table;
}

std::once_flag g_register_once;

void RegisterAll()
{
    RegisterBase();
    RegisterExec();
    RegisterCpu();
    RegisterMem();
    RegisterInput();
    RegisterScreen();
    RegisterModules();
    RegisterSymbols();
    RegisterEvents();
    RegisterCoverage();
    RegisterFlow();
    RegisterCalls();
    RegisterFunc();
    RegisterDisasm();
    RegisterWatch();
    RegisterState();
    RegisterDos();
    RegisterBatch();
}

struct PendingCall {
    std::mutex mutex;
    std::condition_variable done_cv;
    bool done = false;
    bool started = false;   // handler began running on the emulation thread
    bool abandoned = false; // caller gave up (timeout): a not yet started handler must not run
    bool ok = false;
    JsonValue result;
    Error error;
};

// Methods that may run while emu.step/step_over executes a callback that waits inside the emulation loop
// (e.g. INT 16h waiting for a key re-enters the loop, and the queue is pumped there): input is what ends the
// wait; reading the screen and the status has no effect on the interrupted instruction.
bool AllowedDuringStep(const std::string& method)
{
    return method.compare(0, 6, "input.") == 0 || method.compare(0, 7, "screen.") == 0 || method == "emu.status";
}

bool RunOnEmulationThread(const Handler& handler, const JsonValue& params, uint32_t timeout_ms,
                          bool allowed_during_step, JsonValue* result, Error* error)
{
    std::shared_ptr<PendingCall> call = std::make_shared<PendingCall>();
    const std::uint64_t ticket = dosbox_agent::AGENT_EmulationQueue().Submit(
            [call, handler, params, allowed_during_step](std::uint64_t) {
                {
                    std::lock_guard<std::mutex> lock(call->mutex);
                    if (call->abandoned)
                        return; // caller already got "timeout": never execute the action late
                    call->started = true;
                }
                JsonValue local_result = JsonValue::Object();
                Error local_error;
                bool ok = false;
                try {
                    if (InStep() && !allowed_during_step)
                        // Nested inside a step: a state.load, func.start or second step here would replace the
                        // machine state under the half-executed instruction.
                        ok = Fail(&local_error, "busy", "a step is still executing (a BIOS/DOS call waits, e.g. "
                                  "INT 16h for a key: send input.* to let it finish)");
                    else
                        ok = handler(params, &local_result, &local_error);
                } catch (...) {
                    ok = Fail(&local_error, "internal", "exception in emulation-thread handler");
                }
                std::lock_guard<std::mutex> lock(call->mutex);
                call->ok = ok;
                call->result = local_result;
                call->error = local_error;
                call->done = true;
                call->done_cv.notify_all();
            });
    if (ticket == 0)
        return Fail(error, "instance_dead", "emulation-thread queue is not available");

    std::unique_lock<std::mutex> lock(call->mutex);
    if (!call->done_cv.wait_for(lock, std::chrono::milliseconds(timeout_ms), [call]() { return call->done; })) {
        call->abandoned = true;
        if (call->started)
            return Fail(error, "timeout",
                        "emulation thread did not finish in time; the action had already started and may have "
                        "executed (partly)");
        return Fail(error, "timeout",
                    "emulation thread did not answer in time, the action was not executed (emulator blocked in a "
                    "dialog, the built-in debugger or the pause menu?)");
    }
    *result = call->result;
    *error = call->error;
    return call->ok;
}

} // namespace

int& QuietDepth()
{
    static int depth = 0;
    return depth;
}

void Register(const std::string& method, Handler handler, bool on_emulation_thread)
{
    Entry entry;
    entry.handler = handler;
    entry.on_emulation_thread = on_emulation_thread;
    Table()[method] = entry;
}

JsonValue Str(const std::string& value) { return JsonValue::String(value); }
JsonValue Num(uint64_t value) { return JsonValue::Number(std::to_string(value)); }
JsonValue Bool(bool value) { return JsonValue::Bool(value); }

JsonValue NumF(double value)
{
    char buffer[64];
    std::snprintf(buffer, sizeof(buffer), "%.6g", value);
    return JsonValue::Number(buffer);
}

JsonValue Hex(uint64_t value, int digits)
{
    char buffer[32];
    std::snprintf(buffer, sizeof(buffer), "%0*llX", digits, static_cast<unsigned long long>(value));
    return JsonValue::String(buffer);
}

JsonValue HexOut(uint64_t value, int digits, bool file_format)
{
    char buffer[40];
    std::snprintf(buffer, sizeof(buffer), file_format ? "0x%0*llX" : "%0*llX", digits,
                  static_cast<unsigned long long>(value));
    return JsonValue::String(buffer);
}

JsonValue NumFixed(double value)
{
    char buffer[64];
    std::snprintf(buffer, sizeof(buffer), "%.3f", value);
    return JsonValue::Number(buffer);
}

bool HexField(const JsonValue& object, const std::string& name, uint32_t* out)
{
    const JsonValue* value = Param(object, name);
    if (value == nullptr)
        return false;
    std::string text = value->text;
    int base = 10;
    if (value->type == JsonType::String) {
        base = 16;
        if (text.size() > 2 && text[0] == '0' && (text[1] == 'x' || text[1] == 'X'))
            text = text.substr(2);
    } else if (value->type != JsonType::Number) {
        return false;
    }
    if (text.empty() || text[0] == '-')
        return false;
    char* end = nullptr;
    errno = 0;
    const unsigned long long parsed = std::strtoull(text.c_str(), &end, base);
    if (*end != '\0' || errno != 0 || parsed > 0xFFFFFFFFull)
        return false;
    *out = static_cast<uint32_t>(parsed);
    return true;
}

void Set(JsonValue* object, const std::string& name, const JsonValue& value)
{
    object->type = JsonType::Object;
    object->object[name] = value;
}

const JsonValue* Param(const JsonValue& params, const std::string& name)
{
    if (params.type != JsonType::Object)
        return nullptr;
    return params.Find(name);
}

bool ParamStr(const JsonValue& params, const std::string& name, std::string* out)
{
    const JsonValue* value = Param(params, name);
    if (value == nullptr || value->type != JsonType::String)
        return false;
    *out = value->text;
    return true;
}

bool ParamU64(const JsonValue& params, const std::string& name, uint64_t* out)
{
    const JsonValue* value = Param(params, name);
    if (value == nullptr || (value->type != JsonType::Number && value->type != JsonType::String))
        return false;
    const char* text = value->text.c_str();
    char* end = nullptr;
    errno = 0;
    // "0x" prefix = hex, everything else decimal (never octal: "0100" is 100).
    const bool hex = text[0] == '0' && (text[1] == 'x' || text[1] == 'X');
    const unsigned long long parsed = std::strtoull(text, &end, hex ? 16 : 10);
    if (end == text || *end != '\0' || errno != 0 || text[0] == '-')
        return false;
    *out = parsed;
    return true;
}

bool ParamBool(const JsonValue& params, const std::string& name, bool* out)
{
    const JsonValue* value = Param(params, name);
    if (value == nullptr || value->type != JsonType::Bool)
        return false;
    *out = value->boolean;
    return true;
}

bool Fail(Error* error, const std::string& code, const std::string& message)
{
    error->code = code;
    error->message = message;
    return false;
}

bool PlainName(const std::string& name)
{
    if (name.empty() || name.find_first_of("\\/:*?\"<>|") != std::string::npos || name.find("..") != std::string::npos)
        return false;
    if (name.back() == '.' || name.back() == ' ')
        return false;
    // Windows device names (also with an extension) would write to the device instead of a file.
    std::string stem = name.substr(0, name.find('.'));
    while (!stem.empty() && stem.back() == ' ')
        stem.pop_back();
    for (char& c : stem)
        c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    if (stem == "CON" || stem == "PRN" || stem == "AUX" || stem == "NUL")
        return false;
    if (stem.size() == 4 && (stem.compare(0, 3, "COM") == 0 || stem.compare(0, 3, "LPT") == 0) && stem[3] >= '0' && stem[3] <= '9')
        return false;
    return true;
}

std::string OutputPath(const std::string& file_name)
{
    const char* dir = std::getenv("DMCP_OUTDIR");
    if (dir == nullptr || dir[0] == '\0')
        return file_name;
    std::string path(dir);
#ifdef WIN32
    const char separator = '\\';
#else
    const char separator = '/';
#endif
    if (path.back() != '\\' && path.back() != '/')
        path.push_back(separator);
    return path + file_name;
}

bool WriteOutputFile(const std::string& file_name, const void* data, size_t size,
                     std::string* full_path, Error* error)
{
    if (!PlainName(file_name))
        return Fail(error, "bad_argument", "file name must be a plain name without path characters: " + file_name);
    const std::string path = OutputPath(file_name);
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    if (!out)
        return Fail(error, "internal", "cannot open output file " + path);
    out.write(static_cast<const char*>(data), static_cast<std::streamsize>(size));
    if (!out)
        return Fail(error, "internal", "cannot write output file " + path);
    *full_path = path;
    return true;
}

void RegisterBase()
{
    Register("dmcp.ping", [](const JsonValue&, JsonValue* result, Error*) {
        Set(result, "pong", Bool(true));
        Set(result, "protocol", Str("dmcp/1"));
        return true;
    }, false);
    Register("dmcp.ping_emu", [](const JsonValue&, JsonValue* result, Error*) {
        Set(result, "pong", Bool(true));
        return true;
    });
    Register("dmcp.methods", [](const JsonValue&, JsonValue* result, Error*) {
        JsonValue list = JsonValue::Array();
        for (const auto& entry : Table())
            list.array.push_back(Str(entry.first));
        Set(result, "methods", list);
        return true;
    }, false);
}

} // namespace dmcp

bool DMCP_HandleRequest(const std::string& request, std::string* response)
{
    using namespace dosbox_agent;
    std::call_once(dmcp::g_register_once, dmcp::RegisterAll);

    JsonRpcRequest parsed;
    JsonRpcParseError kind = JsonRpcParseError::ParseError;
    std::string parse_error;
    if (!AGENT_ParseJsonRpcRequest(request, &parsed, &kind, &parse_error))
        return false; // upstream answers parse errors
    const auto found = dmcp::Table().find(parsed.method);
    if (found == dmcp::Table().end()) {
        // "state." is shared with the upstream agent (state.get_registers): state.save/load are registered by name
        static const char* const kOwnPrefixes[] = {"dmcp.", "emu.", "cpu.", "mem.", "input.", "screen.", "events.",
                                                   "coverage.", "flow.", "modules.", "symbols.", "watch.", "dos.", "batch."};
        bool ours = false;
        for (const char* prefix : kOwnPrefixes)
            ours = ours || parsed.method.compare(0, std::strlen(prefix), prefix) == 0;
        if (!ours)
            return false; // not ours: upstream handles or rejects it
        JsonValue data = JsonValue::Object();
        dmcp::Set(&data, "code", dmcp::Str("bad_argument"));
        *response = AGENT_MakeJsonRpcError(parsed.id, -32000, "unknown method " + parsed.method + " (see dmcp.methods)", &data);
        return true;
    }

    uint64_t timeout_ms = 10000;
    dmcp::ParamU64(parsed.params, "timeout_ms", &timeout_ms);
    if (timeout_ms < 1)
        timeout_ms = 1;
    if (timeout_ms > 600000)
        timeout_ms = 600000;

    JsonValue result = JsonValue::Object();
    dmcp::Error error;
    bool ok;
    if (found->second.on_emulation_thread)
        ok = dmcp::RunOnEmulationThread(found->second.handler, parsed.params, static_cast<uint32_t>(timeout_ms),
                                        dmcp::AllowedDuringStep(parsed.method), &result, &error);
    else
        ok = found->second.handler(parsed.params, &result, &error);

    if (ok) {
        *response = AGENT_MakeJsonRpcResult(parsed.id, result);
    } else {
        JsonValue data = JsonValue::Object();
        dmcp::Set(&data, "code", dmcp::Str(error.code));
        *response = AGENT_MakeJsonRpcError(parsed.id, -32000, error.message, &data);
    }
    return true;
}

#endif // C_DMCP
