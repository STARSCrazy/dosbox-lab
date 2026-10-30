// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "cpu.h"
#include "paging.h"
#include "regs.h"
#include "dmcp_addr.h"
#include "dmcp_record.h"

#include <set>

namespace dmcp {
namespace {

struct ModeState {
    bool pmode = false;
    bool v86 = false;
    bool paging = false;
    uint32_t cr3 = 0;
};

uint64_t g_generation = 0;
bool g_known = false;
ModeState g_last;
bool g_sel_valid = false;
uint16_t g_last_sel = 0;
uint32_t g_last_base = 0;
std::set<uint64_t> g_seen; // (selector << 32) | base

ModeState Current()
{
    ModeState s;
    s.pmode = cpu.pmode;
    s.v86 = GETFLAG(VM) != 0;
    s.paging = (cpu.cr0 & 0x80000000u) != 0;
    s.cr3 = static_cast<uint32_t>(PAGING_GetDirBase());
    return s;
}

bool Same(const ModeState& a, const ModeState& b)
{
    return a.pmode == b.pmode && a.v86 == b.v86 && a.paging == b.paging && a.cr3 == b.cr3;
}

JsonValue StateJson(const ModeState& s)
{
    JsonValue o = JsonValue::Object();
    Set(&o, "modus", Str(!s.pmode ? "real" : (s.v86 ? "v86" : "pm")));
    Set(&o, "paging", Bool(s.paging));
    Set(&o, "cr3", HexOut(s.cr3, 8, true));
    return o;
}

} // namespace

void ModeOnInstruction()
{
    if (g_generation != EventsGeneration()) {
        g_generation = EventsGeneration();
        g_known = false;
        g_sel_valid = false;
        g_seen.clear();
    }
    const EventTypes& types = ActiveEventTypes();
    if (types.mode) {
        const ModeState now = Current();
        if (!g_known || !Same(now, g_last)) {
            JsonValue event = JsonValue::Object();
            Set(&event, "von", g_known ? StateJson(g_last) : JsonValue::Null());
            Set(&event, "nach", StateJson(now));
            g_last = now;
            g_known = true;
            EmitEvent("mode", event);
        }
    }
    if (!types.new_code_selector || !cpu.pmode || GETFLAG(VM))
        return;
    const uint16_t sel = static_cast<uint16_t>(SegValue(cs));
    const uint32_t base = static_cast<uint32_t>(SegPhys(cs));
    if (g_sel_valid && sel == g_last_sel && base == g_last_base)
        return;
    g_sel_valid = true;
    g_last_sel = sel;
    g_last_base = base;
    if (!g_seen.insert((static_cast<uint64_t>(sel) << 32) | base).second)
        return;
    JsonValue desc = JsonValue::Object();
    Set(&desc, "basis", HexOut(base, 8, true));
    Set(&desc, "bits", Num(cpu.code.big ? 32 : 16));
    Descriptor d;
    Error ignored;
    if (LoadDescriptor(sel, &d, &ignored)) {
        Set(&desc, "limit", HexOut(static_cast<uint32_t>(d.GetLimit()), 8, true));
        Set(&desc, "dpl", Num(static_cast<uint64_t>(d.DPL())));
    }
    JsonValue event = JsonValue::Object();
    Set(&event, "sel", HexOut(sel, 4, true));
    Set(&event, "deskriptor", desc);
    EmitEvent("new_code_selector", event);
}

} // namespace dmcp

#endif // C_DMCP
