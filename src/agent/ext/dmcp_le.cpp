// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include <algorithm>
#include <string>
#include <vector>

#include "dosbox.h"
#include "cpu.h"
#include "regs.h"
#include "dos_inc.h"
#include "dos_system.h"
#include "dmcp_internal.h"
#include "dmcp_modules.h"

Bitu FillFlags(void);

namespace dmcp {
namespace {

const uint32_t kMaxObjects = 64;
const uint32_t kHeaderSize = 0x48;
const uint32_t kObjectEntry = 24;

uint32_t U32(const uint8_t* b) { return b[0] | (b[1] << 8) | (b[2] << 16) | (static_cast<uint32_t>(b[3]) << 24); }

// A file opened through the drive layer: no SFT entry, no handle, the guest sees nothing.
class DriveFile {
public:
    explicit DriveFile(const char* dos_name)
    {
        char fullname[DOS_PATHLENGTH] = {0};
        uint8_t drive = 0;
        if (!DOS_MakeName(dos_name, fullname, &drive) || drive >= DOS_DRIVES || Drives[drive] == nullptr)
            return;
        if (!Drives[drive]->FileOpen(&file_, fullname, OPEN_READ))
            file_ = nullptr;
        if (file_ == nullptr)
            return;
        file_->AddRef();
        uint32_t end = 0;
        if (file_->Seek(&end, DOS_SEEK_END))
            size_ = end;
    }
    ~DriveFile()
    {
        if (file_ == nullptr)
            return;
        file_->Close();
        if (file_->RemoveRef() <= 0)
            delete file_;
    }
    DriveFile(const DriveFile&) = delete;
    DriveFile& operator=(const DriveFile&) = delete;

    bool Ok() const { return file_ != nullptr; }
    uint32_t Size() const { return size_; }
    // Reads exactly len bytes at pos; false if the range is not inside the file.
    bool ReadAt(uint32_t pos, uint8_t* data, uint32_t len)
    {
        if (file_ == nullptr || pos > size_ || len > size_ - pos)
            return false;
        uint32_t at = pos;
        if (!file_->Seek(&at, DOS_SEEK_SET) || at != pos)
            return false;
        while (len > 0) {
            const uint16_t chunk = static_cast<uint16_t>(std::min<uint32_t>(len, 0x8000));
            uint16_t got = chunk;
            if (!file_->Read(data, &got) || got != chunk)
                return false;
            data += got;
            len -= got;
        }
        return true;
    }

private:
    DOS_File* file_ = nullptr;
    uint32_t size_ = 0;
};

void Unusable(LeInfo* le, const char* why)
{
    le->zuordnung = "kein_le";
    le->hinweis = why;
    le->objects.clear();
}

void ParseLe(DriveFile& file, LeInfo* le)
{
    uint8_t mz[0x40];
    if (!file.ReadAt(0, mz, sizeof(mz)) || mz[0] != 'M' || mz[1] != 'Z')
        return;
    const uint32_t off = U32(mz + 0x3C);
    uint8_t sig[2];
    if (off == 0 || !file.ReadAt(off, sig, 2) || sig[0] != 'L' || sig[1] != 'E')
        return;
    le->vorhanden = true;
    uint8_t hdr[kHeaderSize];
    if (!file.ReadAt(off, hdr, kHeaderSize))
        return Unusable(le, "LE header extends beyond the end of the file");
    const uint32_t eip_object = U32(hdr + 0x18);
    const uint32_t eip = U32(hdr + 0x1C);
    const uint32_t table = U32(hdr + 0x40);
    const uint32_t count = U32(hdr + 0x44);
    if (count < 1 || count > kMaxObjects)
        return Unusable(le, "LE object count out of range (1..64)");
    if (table > file.Size() - off || count * kObjectEntry > file.Size() - off - table)
        return Unusable(le, "LE object table extends beyond the end of the file");
    std::vector<uint8_t> raw(count * kObjectEntry);
    if (!file.ReadAt(off + table, raw.data(), static_cast<uint32_t>(raw.size())))
        return Unusable(le, "LE object table not readable");
    for (uint32_t i = 0; i < count; ++i) {
        LeObject o;
        o.size = U32(&raw[i * kObjectEntry]);
        o.va_start = U32(&raw[i * kObjectEntry + 4]);
        if (o.size == 0)
            return Unusable(le, "LE object with size 0");
        if (o.va_start > 0xFFFFFFFFu - (o.size - 1))
            return Unusable(le, "LE object exceeds the 32-bit address space");
        le->objects.push_back(o);
    }
    std::vector<LeObject> sorted = le->objects;
    std::sort(sorted.begin(), sorted.end(), [](const LeObject& a, const LeObject& b) { return a.va_start < b.va_start; });
    for (size_t i = 1; i < sorted.size(); ++i)
        if (sorted[i].va_start <= sorted[i - 1].va_start + (sorted[i - 1].size - 1))
            return Unusable(le, "LE objects overlap");
    if (eip_object < 1 || eip_object > count)
        return Unusable(le, "LE entry object out of range");
    if (eip >= le->objects[eip_object - 1].size)
        return Unusable(le, "LE entry point outside its object");
    le->eip_object = eip_object - 1;
    le->eip = eip;
    le->zuordnung = "offen";
}

const uint32_t kMaxAllocations = 256;
const uint32_t kPage = 0x1000;

struct PendingInt {
    bool open = false;
    uint16_t cs = 0;
    uint32_t ret_eip = 0;
    bool pm = false;
    uint32_t size = 0;
};

std::string g_pending;     // module with an open derivation, empty if none
PendingInt g_int;          // INT 31h AX=0501h waiting for its return
uint32_t g_allocations = 0;

uint32_t AlignPage(uint32_t v) { return (v + kPage - 1) & ~(kPage - 1); }
bool InPm() { return cpu.pmode && !GETFLAG(VM); }

void StopObserving()
{
    g_pending.clear();
    g_int = PendingInt();
    g_allocations = 0;
    UpdateHookActive();
}

void GiveUp(Module& m, const std::string& why)
{
    m.le.zuordnung = "aufgegeben";
    m.le.hinweis = why;
    StopObserving();
}

void Finish(Module& m)
{
    std::vector<ModuleSegment> segments;
    for (size_t i = 0; i < m.le.objects.size(); ++i) {
        const LeObject& o = m.le.objects[i];
        ModuleSegment s;
        s.name = "obj" + std::to_string(i + 1);
        s.va_start = o.va_start;
        s.va_end = o.va_start + (o.size - 1);
        s.basis_linear = o.linear - o.va_start;
        segments.push_back(s);
    }
    const std::string name = m.name; // SetSegments looks the module up again
    bool applied = false;
    Error error;
    if (!SetSegments(name, segments, Herkunft::Abgeleitet, &applied, &error)) {
        GiveUp(m, error.message);
        return;
    }
    m.le.zuordnung = "fertig";
    if (!applied)
        m.le.hinweis = "a stronger mapping (symbol file or set_segments) has priority";
    StopObserving();
}

void OnAllocation(uint32_t size, uint32_t linear)
{
    Module* m = FindModule(g_pending);
    if (m == nullptr || m->le.zuordnung != "offen") {
        StopObserving(); // module gone or recreated (modules.clear)
        return;
    }
    std::vector<LeObject>& objects = m->le.objects;
    ++g_allocations;
    const bool none_assigned = std::none_of(objects.begin(), objects.end(), [](const LeObject& o) { return o.assigned; });
    uint32_t total = 0;
    for (const LeObject& o : objects)
        total += AlignPage(o.size);
    if (objects.size() > 1 && none_assigned && size == total) {
        uint32_t at = linear; // one block for all objects, each page-aligned
        for (LeObject& o : objects) {
            o.assigned = true;
            o.linear = at;
            at += AlignPage(o.size);
        }
    } else {
        auto next = std::find_if(objects.begin(), objects.end(), [](const LeObject& o) { return !o.assigned; });
        if (next != objects.end() && (size == next->size || size == AlignPage(next->size))) {
            next->assigned = true;
            next->linear = linear;
        }
    }
    if (std::all_of(objects.begin(), objects.end(), [](const LeObject& o) { return o.assigned; }))
        Finish(*m);
    else if (g_allocations >= kMaxAllocations)
        GiveUp(*m, "no complete match within 256 DPMI 0501h allocations");
}

} // namespace

bool LeActive()
{
    return !g_pending.empty();
}

void LeOnSoftwareInterrupt(uint8_t num, uint32_t oldeip)
{
    if (num != 0x31 || reg_ax != 0x0501)
        return;
    g_int.open = true;
    g_int.cs = static_cast<uint16_t>(SegValue(cs));
    g_int.ret_eip = oldeip;
    g_int.pm = InPm();
    g_int.size = (static_cast<uint32_t>(reg_bx) << 16) | reg_cx;
}

void LeOnInstruction()
{
    if (!g_int.open || SegValue(cs) != g_int.cs || reg_eip != g_int.ret_eip || InPm() != g_int.pm)
        return;
    g_int.open = false;
    FillFlags();
    if ((reg_flags & FLAG_CF) != 0)
        return; // allocation failed: not a block
    OnAllocation(g_int.size, (static_cast<uint32_t>(reg_bx) << 16) | reg_cx);
}

void LeOnExit(uint16_t psp)
{
    if (g_pending.empty())
        return;
    Module* m = FindModule(g_pending);
    if (m == nullptr || m->le.zuordnung != "offen") {
        StopObserving(); // module gone or recreated (modules.clear)
        return;
    }
    if (m->has_load && m->psp == psp)
        GiveUp(*m, "program ended before all LE objects were matched");
}

void LeOnExec(const char* raw_name, const std::string& module_name, uint16_t flags)
{
    if (flags == 3)
        return; // overlays have no LE objects of their own
    if (!g_pending.empty()) {
        Module* old = FindModule(g_pending);
        if (old != nullptr && old->name != module_name)
            GiveUp(*old, "another program was started");
        else
            StopObserving(); // same module started again: begin anew
    }
    Module* m = FindModule(module_name);
    if (m == nullptr)
        return;
    m->le = LeInfo();
    // a new run resets the derivation: drop mappings of the previous run (stronger ones stay)
    if (!m->segments.empty() && m->segments.front().herkunft == Herkunft::Abgeleitet)
        m->segments.clear();
    const uint16_t saved_error = dos.errorcode;
    {
        DriveFile file(raw_name);
        if (file.Ok())
            ParseLe(file, &m->le);
    }
    dos.errorcode = saved_error; // the guest must not see an error code from our read
    if (m->le.zuordnung == "offen") {
        g_pending = module_name;
        g_int = PendingInt();
        g_allocations = 0;
        UpdateHookActive();
    }
}

JsonValue LeJson(const LeInfo& le)
{
    JsonValue o = JsonValue::Object();
    JsonValue objects = JsonValue::Array();
    for (size_t i = 0; i < le.objects.size(); ++i) {
        const LeObject& obj = le.objects[i];
        JsonValue e = JsonValue::Object();
        Set(&e, "nr", Num(i + 1));
        Set(&e, "va_start", Hex(obj.va_start, 8));
        Set(&e, "groesse", Hex(obj.size, 8));
        if (obj.assigned)
            Set(&e, "basis_linear", Hex(obj.linear - obj.va_start, 8));
        objects.array.push_back(e);
    }
    Set(&o, "objekte", objects);
    Set(&o, "zuordnung", Str(le.zuordnung));
    if (!le.objects.empty()) {
        const LeObject& entry = le.objects[le.eip_object];
        Set(&o, "einsprung_va", Hex(entry.va_start + le.eip, 8));
        if (le.zuordnung == "fertig")
            Set(&o, "einsprung_linear", Hex(entry.linear + le.eip, 8));
    }
    if (!le.hinweis.empty())
        Set(&o, "hinweis", Str(le.hinweis));
    return o;
}

} // namespace dmcp

#endif // C_DMCP
