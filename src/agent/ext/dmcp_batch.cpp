// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <string>

#include "dosbox.h"
#include "dos_inc.h"
#include "programs.h"
#include "support.h"
#include "agent/dmcp.h"
#include "dmcp_internal.h"

namespace dmcp {
namespace {

const uint64_t kDefaultMaxBytes = 16ull * 1024ull * 1024ull;

struct Stream {
    FILE* file = nullptr;
    std::string path;
    uint64_t bytes = 0;
};

struct BatchState {
    bool active = false;
    bool finished = false;
    uint8_t exit_code = 0;
    uint32_t programs = 0;
    bool has_last_exit = false; // a program ended since START (EXIT in the command keeps this errorlevel)
    uint8_t last_exit = 0;
    uint16_t own_psp = 0;       // PSP of DMCPBAT START until it ended (the next program may reuse the segment)
    uint64_t max_bytes = kDefaultMaxBytes;
    bool truncated = false;
    std::string error; // first open/write failure since START
    Stream out;
    Stream err;
};

BatchState g_batch;
// batch.status runs on the transport thread (it must answer while the emulator shuts down after EXIT):
// every access to g_batch outside the emulation thread and every change of it goes through this mutex.
std::mutex g_mutex;
int g_in_dos_write = 0; // >0 while DOS_WriteFile runs (CON output may call INT 10h internally)

void CloseStream(Stream& s)
{
    if (s.file != nullptr) {
        std::fclose(s.file);
        s.file = nullptr;
    }
}

void SetError(const std::string& message)
{
    if (g_batch.error.empty())
        g_batch.error = message;
}

void OpenStream(Stream& s, const char* name)
{
    CloseStream(s);
    s.path = OutputPath(name);
    s.bytes = 0;
    s.file = std::fopen(s.path.c_str(), "wb");
    if (s.file == nullptr)
        SetError(std::string("cannot open ") + name + " for writing: " + s.path);
}

void Append(Stream& s, const uint8_t* data, uint64_t size)
{
    if (s.file == nullptr || size == 0)
        return;
    if (s.bytes + size > g_batch.max_bytes) {
        size = g_batch.max_bytes - s.bytes;
        g_batch.truncated = true;
    }
    if (size == 0)
        return;
    const size_t written = std::fwrite(data, 1, static_cast<size_t>(size), s.file);
    if (std::fflush(s.file) != 0 || written != size) // flush: a timeout kills the process, keep data on disk
        SetError("write to " + s.path + " failed");
    s.bytes += written;
}

bool EndsWithNoCase(const std::string& text, const char* suffix)
{
    const std::string s(suffix);
    if (text.size() < s.size())
        return false;
    for (size_t i = 0; i < s.size(); ++i)
        if (std::toupper(static_cast<unsigned char>(text[text.size() - s.size() + i])) != s[i])
            return false;
    return true;
}

// batch.json in the output directory: last known state, survives the end of the process (EXIT in the command).
// Caller holds g_mutex.
void WriteStatusFile()
{
    JsonValue status = JsonValue::Object();
    Set(&status, "aktiv", Bool(g_batch.active));
    Set(&status, "fertig", Bool(g_batch.finished));
    if (g_batch.finished)
        Set(&status, "exit_code", Num(g_batch.exit_code));
    Set(&status, "programme", Num(g_batch.programs));
    if (g_batch.has_last_exit)
        Set(&status, "letzter_exit_code", Num(g_batch.last_exit));
    Set(&status, "stdout_bytes", Num(g_batch.out.bytes));
    Set(&status, "stderr_bytes", Num(g_batch.err.bytes));
    Set(&status, "abgeschnitten", Bool(g_batch.truncated));
    const std::string text = dosbox_agent::AGENT_SerializeJson(status) + "\n";
    const std::string path = OutputPath("batch.json");
    FILE* file = std::fopen(path.c_str(), "wb");
    if (file == nullptr) {
        SetError("cannot open batch.json for writing: " + path);
        return;
    }
    const size_t written = std::fwrite(text.data(), 1, text.size(), file);
    if (std::fflush(file) != 0 || written != text.size())
        SetError("write to " + path + " failed");
    std::fclose(file);
}

} // namespace

void BatchStart(uint64_t max_bytes)
{
    std::lock_guard<std::mutex> lock(g_mutex);
    g_batch.active = true;
    g_batch.finished = false;
    g_batch.exit_code = 0;
    g_batch.programs = 0;
    g_batch.has_last_exit = false;
    g_batch.last_exit = 0;
    g_batch.own_psp = dos.psp();
    g_batch.truncated = false;
    g_batch.error.clear();
    g_batch.max_bytes = max_bytes;
    OpenStream(g_batch.out, "stdout.txt");
    OpenStream(g_batch.err, "stderr.txt");
    WriteStatusFile();
}

void BatchEnd()
{
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        g_batch.exit_code = dos.return_code; // errorlevel of the command, as IF ERRORLEVEL sees it
        g_batch.active = false;
        g_batch.finished = true;
        CloseStream(g_batch.out);
        CloseStream(g_batch.err);
        WriteStatusFile();
    }
    StopAt("batch"); // keep the final screen stable until the Python layer has saved it
}

void BatchOnExec(const std::string& path, bool load_and_run)
{
    if (g_batch.active && load_and_run && !EndsWithNoCase(path, "DMCPBAT.COM")) {
        std::lock_guard<std::mutex> lock(g_mutex);
        g_batch.own_psp = 0; // DMCPBAT START has ended, a program loaded at its segment counts
        ++g_batch.programs;
        WriteStatusFile();
    }
}

void BatchOnExit(uint16_t psp, uint8_t exit_code)
{
    if (!g_batch.active)
        return;
    std::lock_guard<std::mutex> lock(g_mutex);
    if (g_batch.own_psp != 0 && psp == g_batch.own_psp) {
        g_batch.own_psp = 0; // DMCPBAT START itself ends: not a program of the command
        return;
    }
    g_batch.has_last_exit = true;
    g_batch.last_exit = exit_code;
    WriteStatusFile();
}

void RegisterBatch()
{
    Register("batch.status", [](const JsonValue&, JsonValue* result, Error*) {
        std::lock_guard<std::mutex> lock(g_mutex);
        *result = JsonValue::Object();
        Set(result, "aktiv", Bool(g_batch.active));
        Set(result, "fertig", Bool(g_batch.finished));
        if (g_batch.finished)
            Set(result, "exit_code", Num(g_batch.exit_code));
        Set(result, "programme", Num(g_batch.programs));
        if (g_batch.has_last_exit)
            Set(result, "letzter_exit_code", Num(g_batch.last_exit));
        Set(result, "stdout_bytes", Num(g_batch.out.bytes));
        Set(result, "stderr_bytes", Num(g_batch.err.bytes));
        Set(result, "abgeschnitten", Bool(g_batch.truncated));
        Set(result, "stdout_pfad", Str(g_batch.out.path));
        Set(result, "stderr_pfad", Str(g_batch.err.path));
        if (!g_batch.error.empty())
            Set(result, "fehler", Str(g_batch.error));
        return true;
    }, false); // transport thread: answers even while the emulator shuts down (EXIT ended the shell)
}

} // namespace dmcp

namespace {

class DMCPBAT : public Program {
public:
    void Run() override
    {
        std::string arg;
        if (!cmd->FindCommand(1, arg)) {
            WriteOut("DMCPBAT START [max_bytes] | ENDE\n");
            return;
        }
        upcase(arg);
        if (arg == "START") {
            uint64_t max_bytes = dmcp::kDefaultMaxBytes;
            std::string limit;
            if (cmd->FindCommand(2, limit)) {
                char* end = nullptr;
                const unsigned long long parsed = std::strtoull(limit.c_str(), &end, 10);
                if (end != limit.c_str() && *end == '\0' && parsed > 0)
                    max_bytes = parsed;
            }
            dmcp::BatchStart(max_bytes); // after any own output: DMCPBAT writes nothing into the capture
        } else if (arg == "ENDE") {
            dmcp::BatchEnd();
        } else {
            WriteOut("DMCPBAT: unknown argument (START or ENDE)\n");
        }
    }
};

void DMCPBAT_ProgramStart(Program** make)
{
    *make = new DMCPBAT;
}

} // namespace

void DMCP_SetupPrograms()
{
    PROGRAMS_MakeFile("DMCPBAT.COM", DMCPBAT_ProgramStart);
}

DMCP_DosWriteScope::DMCP_DosWriteScope(uint16_t entry, const uint8_t* data, uint16_t amount, bool fcb)
{
    ++dmcp::g_in_dos_write;
    if (!dmcp::g_batch.active || fcb)
        return;
    std::lock_guard<std::mutex> lock(dmcp::g_mutex);
    if (entry == 1)
        dmcp::Append(dmcp::g_batch.out, data, amount);
    else if (entry == 2)
        dmcp::Append(dmcp::g_batch.err, data, amount);
}

DMCP_DosWriteScope::~DMCP_DosWriteScope()
{
    --dmcp::g_in_dos_write;
}

void DMCP_OnBiosTty(uint8_t ch)
{
    if (dmcp::g_batch.active && dmcp::g_in_dos_write == 0) {
        std::lock_guard<std::mutex> lock(dmcp::g_mutex);
        dmcp::Append(dmcp::g_batch.out, &ch, 1);
    }
}

#endif // C_DMCP
