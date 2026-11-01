// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "cpu.h"
#include "regs.h"
#include "mem.h"
#include "paging.h"
#include "pic.h"
#include "dmcp_addr.h"
#include "dmcp_internal.h"
#include "dmcp_modules.h"
#include "dmcp_record.h"
#include "dmcp_watch.h"

#include <algorithm>
#include <map>
#include <string>
#include <vector>

namespace dmcp {
namespace {

const uint32_t kMaxPages = 1024;          // all watches together (4 MB)
const uint64_t kMaxLength = 1024 * 1024;  // one watch

struct Watch {
    uint32_t id = 0;
    uint32_t start = 0;   // linear
    uint32_t length = 0;
    bool read = false;
    bool write = true;
    bool stop = true;     // action "halt" (else "log")
    std::string source;
    uint64_t hits = 0;
};
std::vector<Watch> g_watches;
uint32_t g_next_id = 1;

// The TLB entry behind our redirection. A direct-memory entry (RAM/ROM) has a host pointer and a handler without
// byte functions (PageHandler::readb would E_Exit), so the host pointer is used whenever it is set, exactly like
// mem_readb_inline would.
struct PageState {
    bool want_read = false;
    bool want_write = false;
    PageHandler* read = nullptr;   // original handler behind our redirection
    PageHandler* write = nullptr;
    HostPt read_host = nullptr;    // original paging.tlb.read entry (base - linear page address) or nullptr
    HostPt write_host = nullptr;
};
std::map<uint32_t, PageState> g_pages; // linear page number -> state

struct Access {
    uint64_t nr = 0;
    uint32_t watch = 0;
    bool write = false;
    uint32_t linear = 0;
    uint8_t width = 1;
    uint32_t value = 0;
    bool has_old = false;
    uint32_t old = 0;
    CodePoint at;
    double time_ms = 0;
};
std::vector<Access> g_log;
size_t g_log_max = 100000;
bool g_log_full = false;
uint64_t g_access_nr = 0;

CodePoint g_insn;              // instruction being executed
bool g_have_insn = false;
bool g_stop_pending = false;
Access g_stop_access;

void Install(uint32_t page, PageState& st);

class WatchHandler : public PageHandler {
public:
    WatchHandler() : PageHandler(0) {} // no PFLAG_READABLE/WRITEABLE: every access of the page calls us

    uint8_t readb(PhysPt addr) override
    {
        const PageState st = State(addr);
        const uint8_t v = st.read_host ? host_readb(st.read_host + addr) : static_cast<uint8_t>(st.read->readb(addr));
        Seen(false, addr, 1, v, false, 0);
        return v;
    }
    uint16_t readw(PhysPt addr) override
    {
        const PageState st = State(addr);
        const uint16_t v = st.read_host ? host_readw(st.read_host + addr) : static_cast<uint16_t>(st.read->readw(addr));
        Seen(false, addr, 2, v, false, 0);
        return v;
    }
    uint32_t readd(PhysPt addr) override
    {
        const PageState st = State(addr);
        const uint32_t v = st.read_host ? host_readd(st.read_host + addr) : static_cast<uint32_t>(st.read->readd(addr));
        Seen(false, addr, 4, v, false, 0);
        return v;
    }
    void writeb(PhysPt addr, uint8_t val) override
    {
        uint32_t old = 0;
        const bool has_old = Old(addr, 1, &old);
        const PageState st = State(addr);
        if (st.write_host)
            host_writeb(st.write_host + addr, val);
        else
            st.write->writeb(addr, val);
        Seen(true, addr, 1, val, has_old, old);
    }
    void writew(PhysPt addr, uint16_t val) override
    {
        uint32_t old = 0;
        const bool has_old = Old(addr, 2, &old);
        const PageState st = State(addr);
        if (st.write_host)
            host_writew(st.write_host + addr, val);
        else
            st.write->writew(addr, val);
        Seen(true, addr, 2, val, has_old, old);
    }
    void writed(PhysPt addr, uint32_t val) override
    {
        uint32_t old = 0;
        const bool has_old = Old(addr, 4, &old);
        const PageState st = State(addr);
        if (st.write_host)
            host_writed(st.write_host + addr, val);
        else
            st.write->writed(addr, val);
        Seen(true, addr, 4, val, has_old, old);
    }
    // _checked: true means a guest fault was raised (nothing read/written).
    bool readb_checked(PhysPt addr, uint8_t* val) override
    {
        const PageState st = State(addr);
        bool fault = false;
        if (st.read_host)
            *val = host_readb(st.read_host + addr);
        else
            fault = st.read->readb_checked(addr, val);
        if (!fault)
            Seen(false, addr, 1, *val, false, 0);
        return fault;
    }
    bool readw_checked(PhysPt addr, uint16_t* val) override
    {
        const PageState st = State(addr);
        bool fault = false;
        if (st.read_host)
            *val = host_readw(st.read_host + addr);
        else
            fault = st.read->readw_checked(addr, val);
        if (!fault)
            Seen(false, addr, 2, *val, false, 0);
        return fault;
    }
    bool readd_checked(PhysPt addr, uint32_t* val) override
    {
        const PageState st = State(addr);
        bool fault = false;
        if (st.read_host)
            *val = host_readd(st.read_host + addr);
        else
            fault = st.read->readd_checked(addr, val);
        if (!fault)
            Seen(false, addr, 4, *val, false, 0);
        return fault;
    }
    bool writeb_checked(PhysPt addr, uint8_t val) override
    {
        uint32_t old = 0;
        const bool has_old = Old(addr, 1, &old);
        const PageState st = State(addr);
        bool fault = false;
        if (st.write_host)
            host_writeb(st.write_host + addr, val);
        else
            fault = st.write->writeb_checked(addr, val);
        if (!fault)
            Seen(true, addr, 1, val, has_old, old);
        return fault;
    }
    bool writew_checked(PhysPt addr, uint16_t val) override
    {
        uint32_t old = 0;
        const bool has_old = Old(addr, 2, &old);
        const PageState st = State(addr);
        bool fault = false;
        if (st.write_host)
            host_writew(st.write_host + addr, val);
        else
            fault = st.write->writew_checked(addr, val);
        if (!fault)
            Seen(true, addr, 2, val, has_old, old);
        return fault;
    }
    bool writed_checked(PhysPt addr, uint32_t val) override
    {
        uint32_t old = 0;
        const bool has_old = Old(addr, 4, &old);
        const PageState st = State(addr);
        bool fault = false;
        if (st.write_host)
            host_writed(st.write_host + addr, val);
        else
            fault = st.write->writed_checked(addr, val);
        if (!fault)
            Seen(true, addr, 4, val, has_old, old);
        return fault;
    }

private:
    // Our page entries exist only for watched pages, so the lookup always finds the state. Callers copy it:
    // the forwarded call may run guest code (page fault) and with it an RPC that rebuilds g_pages.
    static const PageState& State(PhysPt addr) { return g_pages[static_cast<uint32_t>(addr) >> 12]; }

    static bool Old(PhysPt addr, int width, uint32_t* out)
    {
        uint32_t value = 0;
        for (int i = width - 1; i >= 0; --i) {
            uint8_t b = 0;
            if (!PeekLinear(static_cast<uint32_t>(addr) + static_cast<uint32_t>(i), &b))
                return false;
            value = (value << 8) | b;
        }
        *out = value;
        return true;
    }

    static void Seen(bool write, PhysPt addr, uint8_t width, uint32_t value, bool has_old, uint32_t old);
};

WatchHandler g_handler;

void Install(uint32_t page, PageState& st)
{
    if (st.want_read && (paging.tlb.readhandler[page] != &g_handler || paging.tlb.read[page] != nullptr)) {
        if (paging.tlb.readhandler[page] != &g_handler)
            st.read = paging.tlb.readhandler[page];
        st.read_host = paging.tlb.read[page];
        paging.tlb.read[page] = nullptr;
        paging.tlb.readhandler[page] = &g_handler;
    }
    if (st.want_write && (paging.tlb.writehandler[page] != &g_handler || paging.tlb.write[page] != nullptr)) {
        if (paging.tlb.writehandler[page] != &g_handler)
            st.write = paging.tlb.writehandler[page];
        st.write_host = paging.tlb.write[page];
        paging.tlb.write[page] = nullptr;
        paging.tlb.writehandler[page] = &g_handler;
    }
}

void WatchHandler::Seen(bool write, PhysPt addr, uint8_t width, uint32_t value, bool has_old, uint32_t old)
{
    const uint32_t linear = static_cast<uint32_t>(addr);
    const uint32_t page = linear >> 12;
    // The original handler may have re-linked the page (init/foiling handler): redirect it again now, so the
    // next access of a REP string instruction is seen too.
    const auto found = g_pages.find(page);
    if (found != g_pages.end())
        Install(page, found->second);
    if (QuietDepth() > 0)
        return; // the agent's own reads (disassembler)
    // Instruction fetch is no data read (E9).
    if (!write && g_have_insn && linear - g_insn.linear < 16)
        return;
    for (Watch& w : g_watches) {
        if ((write && !w.write) || (!write && !w.read))
            continue;
        const uint64_t a0 = linear, a1 = static_cast<uint64_t>(linear) + width;
        const uint64_t w0 = w.start, w1 = static_cast<uint64_t>(w.start) + w.length;
        if (a1 <= w0 || a0 >= w1)
            continue;
        ++w.hits;
        Access access;
        access.nr = ++g_access_nr;
        access.watch = w.id;
        access.write = write;
        access.linear = linear;
        access.width = width;
        access.value = value;
        access.has_old = has_old && write;
        access.old = old;
        access.at = g_have_insn ? g_insn : CurrentCodePoint();
        access.time_ms = PIC_FullIndex();
        if (!g_log_full) {
            if (g_log.size() >= g_log_max) {
                g_log_full = true;
                ReportLimit("watch", "access log full (" + std::to_string(g_log_max) + " entries)");
            } else {
                g_log.push_back(access);
            }
        }
        // While paused only emu.step executes guest code; other accesses then come from the agent itself.
        if (w.stop && !g_stop_pending && (!IsPaused() || InStep())) {
            g_stop_pending = true;
            g_stop_access = access;
        }
    }
}

JsonValue AccessJson(const Access& a, bool file_format)
{
    JsonValue object = JsonValue::Object();
    Set(&object, "nr", Num(a.nr));
    Set(&object, "watch", Num(a.watch));
    Set(&object, "kind", Str(a.write ? "write" : "read"));
    Address target;
    target.mode = "linear";
    target.has_linear = true;
    target.linear = a.linear;
    ResolvePhys(&target);
    JsonValue target_json = AddressJson(target, file_format);
    AnnotateAddress(target, &target_json, file_format);
    Set(&object, "target", target_json);
    Set(&object, "width", Num(a.width));
    Set(&object, "value", HexOut(a.value, a.width * 2, file_format));
    if (a.has_old)
        Set(&object, "old", HexOut(a.old, a.width * 2, file_format));
    const Address at = CodePointAddress(a.at);
    JsonValue addr = AddressJson(at, file_format);
    AnnotateAddress(at, &addr, file_format);
    Set(&object, "addr", addr);
    Set(&object, "time_ms", NumFixed(a.time_ms));
    return object;
}

// Drops all redirections and rebuilds them from g_watches (after add/remove).
void Rebuild()
{
    for (auto& item : g_pages) {
        const uint32_t page = item.first;
        if (paging.tlb.readhandler[page] == &g_handler || paging.tlb.writehandler[page] == &g_handler)
            PAGING_UnlinkPages(page, 1); // init handler: the next access left the page again
    }
    g_pages.clear();
    for (const Watch& w : g_watches) {
        const uint32_t first = w.start >> 12;
        const uint32_t last = static_cast<uint32_t>((static_cast<uint64_t>(w.start) + w.length - 1) >> 12);
        for (uint32_t page = first; page <= last && page < TLB_SIZE; ++page) {
            PageState& st = g_pages[page];
            st.want_read = st.want_read || w.read;
            st.want_write = st.want_write || w.write;
        }
    }
    for (auto& item : g_pages)
        Install(item.first, item.second);
    UpdateHookActive();
}

size_t PagesWith(const Watch& extra)
{
    std::map<uint32_t, bool> pages;
    std::vector<Watch> all = g_watches;
    all.push_back(extra);
    for (const Watch& w : all) {
        const uint32_t first = w.start >> 12;
        const uint32_t last = static_cast<uint32_t>((static_cast<uint64_t>(w.start) + w.length - 1) >> 12);
        for (uint32_t page = first; page <= last; ++page)
            pages[page] = true;
    }
    return pages.size();
}

JsonValue WatchList()
{
    JsonValue list = JsonValue::Array();
    for (const Watch& w : g_watches) {
        JsonValue entry = JsonValue::Object();
        Set(&entry, "id", Num(w.id));
        Set(&entry, "start", Hex(w.start, 8));
        Set(&entry, "length", Num(w.length));
        Set(&entry, "kind", Str(w.read && w.write ? "both" : w.read ? "read" : "write"));
        Set(&entry, "action", Str(w.stop ? "halt" : "log"));
        Set(&entry, "source", Str(w.source));
        Set(&entry, "hits", Num(w.hits));
        list.array.push_back(entry);
    }
    return list;
}

JsonValue LogInfo()
{
    JsonValue info = JsonValue::Object();
    Set(&info, "entries", Num(g_log.size()));
    Set(&info, "max", Num(g_log_max));
    Set(&info, "full", Bool(g_log_full));
    return info;
}

} // namespace

bool WatchesActive() { return !g_watches.empty(); }

void WatchOnInstruction()
{
    g_insn = CurrentCodePoint();
    g_have_insn = true;
    for (auto& item : g_pages)
        Install(item.first, item.second);
}

bool WatchTakeStop()
{
    if (!g_stop_pending)
        return false;
    g_stop_pending = false;
    JsonValue event = AccessJson(g_stop_access, true);
    EmitEvent("watch", event);
    return true;
}

void WatchDropPending() { g_stop_pending = false; }

JsonValue WatchStopJson() { return AccessJson(g_stop_access, false); }

void RegisterWatch()
{
    Register("watch.add", [](const JsonValue& params, JsonValue* result, Error* error) {
        if (!RequireHookCore("watch.add", error))
            return false;
        Watch w;
        std::string text;
        if (!ParamStr(params, "address", &text))
            return Fail(error, "bad_argument", "address (string) is required");
        Address address;
        if (!ParseAddress(text, &address, error))
            return false;
        JsonValue hints = JsonValue::Array();
        if (address.has_linear) {
            w.start = address.linear;
        } else {
            if ((cpu.cr0 & 0x80000000u) != 0)
                return Fail(error, "bad_argument", "physical watches need paging off; watch the linear address");
            w.start = address.phys;
            hints.array.push_back(Str("p: watched as a linear address (paging off, linear = physical)"));
        }
        uint64_t length = 1;
        if (Param(params, "length") != nullptr && !ParamU64(params, "length", &length))
            return Fail(error, "bad_argument", "length must be a number");
        if (length == 0 || length > kMaxLength || static_cast<uint64_t>(w.start) + length > 0x100000000ull)
            return Fail(error, "bad_argument", "length must be between 1 and 1048576 and stay below 4 GB");
        w.length = static_cast<uint32_t>(length);
        std::string kind = "write", action = "halt";
        if (Param(params, "kind") != nullptr && !ParamStr(params, "kind", &kind))
            return Fail(error, "bad_argument", "kind must be a string");
        if (Param(params, "action") != nullptr && !ParamStr(params, "action", &action))
            return Fail(error, "bad_argument", "action must be a string");
        if (kind != "read" && kind != "write" && kind != "both")
            return Fail(error, "bad_argument", "kind must be read, write or both");
        if (action != "halt" && action != "log")
            return Fail(error, "bad_argument", "action must be halt or log");
        w.read = kind != "write";
        w.write = kind != "read";
        w.stop = action == "halt";
        w.source = text;
        if (PagesWith(w) > kMaxPages)
            return Fail(error, "bad_argument", "too many watched pages (all watches together: at most 1024)");
        uint32_t phys = 0;
        Error ignored;
        // Paging off maps every linear address to itself, so "mapped" also needs the page to be in guest RAM.
        if (!LinearToPhys(w.start, &phys, &ignored) || !PhysInRam(phys, 1))
            hints.array.push_back(Str("page is not mapped at the moment; the watchpoint takes effect as soon as it is"));
        w.id = g_next_id++;
        g_watches.push_back(w);
        Rebuild();
        Set(result, "id", Num(w.id));
        Set(result, "start", Hex(w.start, 8));
        Set(result, "length", Num(w.length));
        Set(result, "pages", Num(((static_cast<uint64_t>(w.start) + w.length - 1) >> 12) - (w.start >> 12) + 1));
        Set(result, "hints", hints);
        return true;
    });
    Register("watch.remove", [](const JsonValue& params, JsonValue* result, Error* error) {
        bool all = false;
        bool removed = false;
        uint64_t id = 0;
        if (ParamBool(params, "all", &all) && all) {
            removed = !g_watches.empty();
            g_watches.clear();
        } else if (ParamU64(params, "id", &id)) {
            for (size_t i = g_watches.size(); i-- > 0;) {
                if (g_watches[i].id == id) {
                    g_watches.erase(g_watches.begin() + static_cast<long>(i));
                    removed = true;
                }
            }
        } else {
            return Fail(error, "bad_argument", "pass id or all:true");
        }
        Rebuild();
        // A pending stop of a removed watch must not stop later.
        if (g_stop_pending && std::none_of(g_watches.begin(), g_watches.end(),
                                           [](const Watch& w) { return w.id == g_stop_access.watch; }))
            g_stop_pending = false;
        Set(result, "removed", Bool(removed));
        Set(result, "watchpoint", WatchList());
        return true;
    });
    Register("watch.list", [](const JsonValue&, JsonValue* result, Error*) {
        Set(result, "watchpoint", WatchList());
        Set(result, "log", LogInfo());
        return true;
    });
    Register("watch.log", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string name = "accesses";
        bool clear = false;
        if (Param(params, "name") != nullptr && !ParamStr(params, "name", &name))
            return Fail(error, "bad_argument", "name must be a string");
        if (Param(params, "clear") != nullptr && !ParamBool(params, "clear", &clear))
            return Fail(error, "bad_argument", "clear must be true or false");
        std::string body;
        for (const Access& a : g_log)
            body += dosbox_agent::AGENT_SerializeJson(AccessJson(a, true)) + "\n";
        std::string path;
        if (!WriteOutputFile(name + ".jsonl", body.data(), body.size(), &path, error))
            return false;
        Set(result, "path", Str(path));
        Set(result, "count", Num(g_log.size()));
        Set(result, "full", Bool(g_log_full));
        if (clear) {
            g_log.clear();
            g_log_full = false;
        }
        return true;
    });
    Register("watch.clear_log", [](const JsonValue& params, JsonValue* result, Error* error) {
        uint64_t max = g_log_max;
        if (Param(params, "max_entries") != nullptr &&
            (!ParamU64(params, "max_entries", &max) || max < 1000 || max > 1000000))
            return Fail(error, "bad_argument", "max_entries must be between 1000 and 1000000");
        g_log.clear();
        g_log_full = false;
        g_log_max = static_cast<size_t>(max);
        Set(result, "log", LogInfo());
        return true;
    });
}

} // namespace dmcp

#endif // C_DMCP
