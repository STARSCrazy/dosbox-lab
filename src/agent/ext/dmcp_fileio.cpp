#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "callback.h"
#include "cpu.h"
#include "dos_inc.h"
#include "regs.h"
#include "dmcp_addr.h"
#include "dmcp_record.h"

#include <algorithm>
#include <vector>

Bitu FillFlags(void);

namespace dmcp {
namespace {

// A DOS read/write (INT 21h 3Fh/40h) waiting for its return.
struct Access {
    bool pm = false;
    uint16_t cs = 0;
    uint32_t ret_eip = 0;
    JsonValue event;    // fields measured at the call
    Address buffer;
    bool wide = false;  // 32-bit registers (32-bit PM client)
};

// A protected-mode INT 21h read/write waiting for its return: real-mode INT 21h inside it are the extender
// passing the call on and get no event of their own. Only 3Fh/40h are tracked: those are what an extender
// passes on (and would duplicate); other functions may never return (DOS/4GW FF01h, EXEC) and would
// otherwise suppress all later real-mode events.
struct PmInt {
    uint16_t cs = 0;
    uint32_t ret_eip = 0;
};

const size_t kMaxOpen = 16;
std::vector<Access> g_open;
std::vector<PmInt> g_pm_open;
uint64_t g_generation = 0;

bool InPm() { return cpu.pmode && !GETFLAG(VM); }

void CheckGeneration()
{
    if (g_generation != EventsGeneration()) {
        g_open.clear();
        g_pm_open.clear();
        g_generation = EventsGeneration();
    }
}

std::string Printable(const char* name)
{
    std::string out;
    for (size_t i = 0; name != nullptr && name[i] != 0 && i < 260; ++i) {
        const unsigned char c = static_cast<unsigned char>(name[i]);
        out.push_back(c >= 0x20 && c < 0x7F ? static_cast<char>(c) : '?');
    }
    return out;
}

std::string HexBytes(const uint8_t* data, size_t len)
{
    static const char digits[] = "0123456789ABCDEF";
    std::string out;
    for (size_t i = 0; i < len; ++i) {
        out.push_back(digits[data[i] >> 4]);
        out.push_back(digits[data[i] & 15]);
    }
    return out;
}

void Finish(const Access& a)
{
    FillFlags();
    JsonValue event = a.event;
    if ((reg_flags & FLAG_CF) != 0) {
        Set(&event, "fehler", HexOut(reg_ax, 4, true));
    } else {
        const uint32_t done = a.wide ? reg_eax : reg_ax;
        Set(&event, "ergebnis", HexOut(done, 8, true));
        const size_t n = std::min<size_t>(done, DateiVorschau());
        if (n > 0) {
            std::vector<uint8_t> raw(n);
            RangeList missing;
            ReadAddress(a.buffer, raw.data(), n, &missing);
            size_t len = n;
            for (const auto& r : missing)
                len = a.buffer.has_linear && r.first >= a.buffer.linear
                          ? std::min<size_t>(len, r.first - a.buffer.linear) : 0;
            if (len > 0)
                Set(&event, "vorschau", Str(HexBytes(raw.data(), len)));
        }
    }
    EmitEvent("datei", event);
}

} // namespace

void FileIoOnSoftwareInterrupt(uint8_t num, uint32_t oldeip)
{
    CheckGeneration();
    if (num != 0x21 || !ActiveEventTypes().datei)
        return;
    const bool pm = InPm();
    if (!pm && SegValue(cs) == CB_SEG)
        return; // DOSBox-internal call from a callback stub
    if (reg_ah != 0x3F && reg_ah != 0x40)
        return;
    if (pm) {
        if (g_pm_open.size() >= kMaxOpen)
            g_pm_open.erase(g_pm_open.begin());
        g_pm_open.push_back(PmInt{static_cast<uint16_t>(SegValue(cs)), oldeip});
    } else if (!g_pm_open.empty()) {
        return; // the extender passes a protected-mode read/write on
    }
    const uint16_t handle = reg_bx;
    const uint8_t idx = RealHandle(handle);
    DOS_File* file = (idx != 0xFF && idx < DOS_FILES) ? Files[idx] : nullptr;
    // Device (CON, NUL, ...). No GetInformation(): for CON it runs the guest's INT 16h, which breaks
    // a protected-mode program inside CPU_Interrupt. All devices derive from DOS_Device.
    if (file != nullptr && dynamic_cast<DOS_Device*>(file) != nullptr)
        return;
    Access a;
    a.pm = pm;
    a.cs = static_cast<uint16_t>(SegValue(cs));
    a.ret_eip = oldeip;
    a.wide = pm && cpu.code.big;
    a.buffer = SegCacheAddress(ds, a.wide ? reg_edx : reg_dx);
    JsonValue e = JsonValue::Object();
    Set(&e, "adr", AddressJson(SegCacheAddress(cs, reg_eip), true));
    Set(&e, "art", Str(reg_ah == 0x3F ? "lesen" : "schreiben"));
    Set(&e, "handle", HexOut(handle, 4, true));
    if (file != nullptr && file->IsOpen()) { // a closed handle may still sit in Files[]: no name/seek through it
        Set(&e, "datei", Str(Printable(file->GetName())));
        const uint32_t pos = file->GetSeekPos();
        if (pos != 0xFFFFFFFFu)
            Set(&e, "offset", HexOut(pos, 8, true));
    }
    Set(&e, "laenge", HexOut(a.wide ? reg_ecx : reg_cx, 8, true));
    Set(&e, "puffer", AddressJson(a.buffer, true));
    Set(&e, "modus", Str(!cpu.pmode ? "real" : (GETFLAG(VM) ? "v86" : "pm")));
    if (CallsActive()) {
        const uint64_t id = CallsCurrentId();
        if (id != 0)
            Set(&e, "call_id", Num(id));
    }
    a.event = e;
    if (g_open.size() >= kMaxOpen)
        g_open.erase(g_open.begin());
    g_open.push_back(a);
}

bool FileIoOpen() { return !g_open.empty() || !g_pm_open.empty(); }

void FileIoOnInstruction()
{
    CheckGeneration();
    const uint16_t sel = static_cast<uint16_t>(SegValue(cs));
    const uint32_t eip = reg_eip;
    const bool pm = InPm();
    for (size_t i = g_open.size(); i-- > 0;) {
        const Access& a = g_open[i];
        if (a.cs != sel || a.ret_eip != eip || a.pm != pm)
            continue;
        Finish(a);
        g_open.resize(i);
        break;
    }
    if (pm) {
        for (size_t i = g_pm_open.size(); i-- > 0;) {
            if (g_pm_open[i].cs == sel && g_pm_open[i].ret_eip == eip) {
                g_pm_open.resize(i);
                break;
            }
        }
    }
}

void FileIoOnExit(uint16_t)
{
    g_open.clear();
    g_pm_open.clear();
}

void FileIoOnStateLoad()
{
    g_open.clear();
    g_pm_open.clear();
}

} // namespace dmcp

#endif // C_DMCP
