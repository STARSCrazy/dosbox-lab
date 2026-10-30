// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "agent/dmcp.h"
#include "mem.h"
#include "render.h"
#include "vga.h"
#include "ints/int10.h"
#include "dmcp_internal.h"

#include <zlib.h>

#include <cstring>
#include <vector>

extern unsigned char* scalerSourceCacheBuffer;
extern unsigned int scalerSourceCacheBufferSize;
extern Render_t render;

namespace dmcp {
namespace {

struct Frame {
    bool valid = false;
    unsigned width = 0, height = 0, bpp = 0, pitch = 0;
    // Video mode geometry at the time the frame was finished (game pixels).
    bool text_mode = false;
    unsigned game_width = 0, game_height = 0;
    std::vector<uint8_t> pixels;
    uint8_t palette[256][3];
};

Frame g_frame;

void ToRgb(const Frame& f, std::vector<uint8_t>* rgb)
{
    rgb->resize(static_cast<size_t>(f.width) * f.height * 3);
    for (unsigned y = 0; y < f.height; ++y) {
        const uint8_t* line = f.pixels.data() + static_cast<size_t>(y) * f.pitch;
        uint8_t* out = rgb->data() + static_cast<size_t>(y) * f.width * 3;
        for (unsigned x = 0; x < f.width; ++x) {
            uint8_t r = 0, g = 0, b = 0;
            if (f.bpp == 8) {
                const uint8_t index = line[x];
                r = f.palette[index][0]; g = f.palette[index][1]; b = f.palette[index][2];
            } else if (f.bpp == 15) {
                uint16_t v; std::memcpy(&v, line + x * 2, 2);
                r = static_cast<uint8_t>(((v >> 10) & 31) * 255 / 31);
                g = static_cast<uint8_t>(((v >> 5) & 31) * 255 / 31);
                b = static_cast<uint8_t>((v & 31) * 255 / 31);
            } else if (f.bpp == 16) {
                uint16_t v; std::memcpy(&v, line + x * 2, 2);
                r = static_cast<uint8_t>(((v >> 11) & 31) * 255 / 31);
                g = static_cast<uint8_t>(((v >> 5) & 63) * 255 / 63);
                b = static_cast<uint8_t>((v & 31) * 255 / 31);
            } else { // 32
                uint32_t v; std::memcpy(&v, line + x * 4, 4);
                r = static_cast<uint8_t>((v >> 16) & 255);
                g = static_cast<uint8_t>((v >> 8) & 255);
                b = static_cast<uint8_t>(v & 255);
            }
            out[x * 3] = r; out[x * 3 + 1] = g; out[x * 3 + 2] = b;
        }
    }
}

void PutU32(std::vector<uint8_t>* out, uint32_t v)
{
    out->push_back(static_cast<uint8_t>(v >> 24)); out->push_back(static_cast<uint8_t>(v >> 16));
    out->push_back(static_cast<uint8_t>(v >> 8)); out->push_back(static_cast<uint8_t>(v));
}

void PutChunk(std::vector<uint8_t>* out, const char* type, const std::vector<uint8_t>& body)
{
    PutU32(out, static_cast<uint32_t>(body.size()));
    const size_t start = out->size();
    out->insert(out->end(), type, type + 4);
    out->insert(out->end(), body.begin(), body.end());
    const uLong crc = crc32(0L, out->data() + start, static_cast<uInt>(out->size() - start));
    PutU32(out, static_cast<uint32_t>(crc));
}

bool EncodePng(const std::vector<uint8_t>& rgb, unsigned width, unsigned height, std::vector<uint8_t>* png)
{
    std::vector<uint8_t> raw;
    raw.reserve(static_cast<size_t>(height) * (width * 3 + 1));
    for (unsigned y = 0; y < height; ++y) {
        raw.push_back(0); // filter: none
        const uint8_t* line = rgb.data() + static_cast<size_t>(y) * width * 3;
        raw.insert(raw.end(), line, line + width * 3);
    }
    uLongf packed_size = compressBound(static_cast<uLong>(raw.size()));
    std::vector<uint8_t> packed(packed_size);
    if (compress2(packed.data(), &packed_size, raw.data(), static_cast<uLong>(raw.size()), 6) != Z_OK)
        return false;
    packed.resize(packed_size);
    const uint8_t signature[] = {0x89, 'P', 'N', 'G', '\r', '\n', 0x1A, '\n'};
    png->assign(signature, signature + 8);
    std::vector<uint8_t> header;
    PutU32(&header, width);
    PutU32(&header, height);
    header.push_back(8); header.push_back(2); header.push_back(0); header.push_back(0); header.push_back(0);
    PutChunk(png, "IHDR", header);
    PutChunk(png, "IDAT", packed);
    PutChunk(png, "IEND", std::vector<uint8_t>());
    return true;
}

// CP437 -> UTF-8 (0x00 = space, 0x01-0x1F as CP437 glyphs, 0x7F = house).
static const char* const kCp437[256] = {
    " ", "\xE2\x98\xBA", "\xE2\x98\xBB", "\xE2\x99\xA5", "\xE2\x99\xA6", "\xE2\x99\xA3", "\xE2\x99\xA0", "\xE2\x80\xA2",
    "\xE2\x97\x98", "\xE2\x97\x8B", "\xE2\x97\x99", "\xE2\x99\x82", "\xE2\x99\x80", "\xE2\x99\xAA", "\xE2\x99\xAB", "\xE2\x98\xBC",
    "\xE2\x96\xBA", "\xE2\x97\x84", "\xE2\x86\x95", "\xE2\x80\xBC", "\xC2\xB6", "\xC2\xA7", "\xE2\x96\xAC", "\xE2\x86\xA8",
    "\xE2\x86\x91", "\xE2\x86\x93", "\xE2\x86\x92", "\xE2\x86\x90", "\xE2\x88\x9F", "\xE2\x86\x94", "\xE2\x96\xB2", "\xE2\x96\xBC",
    " ", "!", "\"", "#", "$", "%", "&", "'",
    "(", ")", "*", "+", ",", "-", ".", "/",
    "0", "1", "2", "3", "4", "5", "6", "7",
    "8", "9", ":", ";", "<", "=", ">", "?",
    "@", "A", "B", "C", "D", "E", "F", "G",
    "H", "I", "J", "K", "L", "M", "N", "O",
    "P", "Q", "R", "S", "T", "U", "V", "W",
    "X", "Y", "Z", "[", "\\", "]", "^", "_",
    "`", "a", "b", "c", "d", "e", "f", "g",
    "h", "i", "j", "k", "l", "m", "n", "o",
    "p", "q", "r", "s", "t", "u", "v", "w",
    "x", "y", "z", "{", "|", "}", "~", "\xE2\x8C\x82",
    "\xC3\x87", "\xC3\xBC", "\xC3\xA9", "\xC3\xA2", "\xC3\xA4", "\xC3\xA0", "\xC3\xA5", "\xC3\xA7",
    "\xC3\xAA", "\xC3\xAB", "\xC3\xA8", "\xC3\xAF", "\xC3\xAE", "\xC3\xAC", "\xC3\x84", "\xC3\x85",
    "\xC3\x89", "\xC3\xA6", "\xC3\x86", "\xC3\xB4", "\xC3\xB6", "\xC3\xB2", "\xC3\xBB", "\xC3\xB9",
    "\xC3\xBF", "\xC3\x96", "\xC3\x9C", "\xC2\xA2", "\xC2\xA3", "\xC2\xA5", "\xE2\x82\xA7", "\xC6\x92",
    "\xC3\xA1", "\xC3\xAD", "\xC3\xB3", "\xC3\xBA", "\xC3\xB1", "\xC3\x91", "\xC2\xAA", "\xC2\xBA",
    "\xC2\xBF", "\xE2\x8C\x90", "\xC2\xAC", "\xC2\xBD", "\xC2\xBC", "\xC2\xA1", "\xC2\xAB", "\xC2\xBB",
    "\xE2\x96\x91", "\xE2\x96\x92", "\xE2\x96\x93", "\xE2\x94\x82", "\xE2\x94\xA4", "\xE2\x95\xA1", "\xE2\x95\xA2", "\xE2\x95\x96",
    "\xE2\x95\x95", "\xE2\x95\xA3", "\xE2\x95\x91", "\xE2\x95\x97", "\xE2\x95\x9D", "\xE2\x95\x9C", "\xE2\x95\x9B", "\xE2\x94\x90",
    "\xE2\x94\x94", "\xE2\x94\xB4", "\xE2\x94\xAC", "\xE2\x94\x9C", "\xE2\x94\x80", "\xE2\x94\xBC", "\xE2\x95\x9E", "\xE2\x95\x9F",
    "\xE2\x95\x9A", "\xE2\x95\x94", "\xE2\x95\xA9", "\xE2\x95\xA6", "\xE2\x95\xA0", "\xE2\x95\x90", "\xE2\x95\xAC", "\xE2\x95\xA7",
    "\xE2\x95\xA8", "\xE2\x95\xA4", "\xE2\x95\xA5", "\xE2\x95\x99", "\xE2\x95\x98", "\xE2\x95\x92", "\xE2\x95\x93", "\xE2\x95\xAB",
    "\xE2\x95\xAA", "\xE2\x94\x98", "\xE2\x94\x8C", "\xE2\x96\x88", "\xE2\x96\x84", "\xE2\x96\x8C", "\xE2\x96\x90", "\xE2\x96\x80",
    "\xCE\xB1", "\xC3\x9F", "\xCE\x93", "\xCF\x80", "\xCE\xA3", "\xCF\x83", "\xC2\xB5", "\xCF\x84",
    "\xCE\xA6", "\xCE\x98", "\xCE\xA9", "\xCE\xB4", "\xE2\x88\x9E", "\xCF\x86", "\xCE\xB5", "\xE2\x88\xA9",
    "\xE2\x89\xA1", "\xC2\xB1", "\xE2\x89\xA5", "\xE2\x89\xA4", "\xE2\x8C\xA0", "\xE2\x8C\xA1", "\xC3\xB7", "\xE2\x89\x88",
    "\xC2\xB0", "\xE2\x88\x99", "\xC2\xB7", "\xE2\x88\x9A", "\xE2\x81\xBF", "\xC2\xB2", "\xE2\x96\xA0", "\xC2\xA0",
};

bool IsTextMode() { return CurMode != nullptr && CurMode->type == M_TEXT; }

// Integer factor between source frame and game pixels (1 = none). Only graphics modes, only exact factors >= 2.
unsigned Factor(bool text_mode, unsigned source, unsigned game)
{
    if (text_mode || game == 0 || source <= game || source % game != 0)
        return 1;
    return source / game;
}

} // namespace

void RegisterScreen()
{
    Register("screen.capture", [](const JsonValue& params, JsonValue* result, Error* error) {
        if (!g_frame.valid)
            return Fail(error, "no_frame", "no frame has been rendered yet");
        std::string name = "screen";
        ParamStr(params, "name", &name);
        const unsigned fx = Factor(g_frame.text_mode, g_frame.width, g_frame.game_width);
        const unsigned fy = Factor(g_frame.text_mode, g_frame.height, g_frame.game_height);
        const unsigned gw = g_frame.width / fx, gh = g_frame.height / fy;
        uint64_t x = 0, y = 0, w = gw, h = gh;
        const bool has_rect = Param(params, "breite") != nullptr;
        if (has_rect && (!ParamU64(params, "x", &x) || !ParamU64(params, "y", &y) ||
                         !ParamU64(params, "breite", &w) || !ParamU64(params, "hoehe", &h)))
            return Fail(error, "bad_argument", "rectangle needs x, y, breite, hoehe (non-negative integers)");
        if (w == 0 || h == 0 || x > gw || w > gw - x || y > gh || h > gh - y)
            return Fail(error, "bad_argument", "rectangle outside the " + std::to_string(gw) + "x" +
                        std::to_string(gh) + " screen");
        std::vector<uint8_t> rgb;
        ToRgb(g_frame, &rgb);
        // Downscale to the game-pixel grid (keep every fx-th column / fy-th row) while cropping.
        std::vector<uint8_t> crop(static_cast<size_t>(w) * h * 3);
        for (uint64_t row = 0; row < h; ++row) {
            const uint8_t* src_line = rgb.data() + static_cast<size_t>((y + row) * fy) * g_frame.width * 3;
            uint8_t* dst = crop.data() + row * w * 3;
            for (uint64_t col = 0; col < w; ++col)
                std::memcpy(dst + col * 3, src_line + static_cast<size_t>((x + col) * fx) * 3, 3);
        }
        std::vector<uint8_t> png;
        if (!EncodePng(crop, static_cast<unsigned>(w), static_cast<unsigned>(h), &png))
            return Fail(error, "internal", "PNG compression failed");
        std::string path;
        if (!WriteOutputFile(name + ".png", png.data(), png.size(), &path, error))
            return false;
        Set(result, "pfad", Str(path));
        Set(result, "breite", Num(w));
        Set(result, "hoehe", Num(h));
        JsonValue source = JsonValue::Object();
        Set(&source, "breite", Num(g_frame.width));
        Set(&source, "hoehe", Num(g_frame.height));
        Set(&source, "bpp", Num(g_frame.bpp));
        Set(result, "quelle", source);
        JsonValue factor = JsonValue::Object();
        Set(&factor, "x", Num(fx));
        Set(&factor, "y", Num(fy));
        Set(result, "faktor", factor);
        return true;
    });
    Register("screen.text", [](const JsonValue&, JsonValue* result, Error* error) {
        if (!IsTextMode())
            return Fail(error, "not_text_mode", "current video mode is not a text mode");
        const uint8_t mode = real_readb(0x40, 0x49);
        const uint16_t cols = real_readw(0x40, 0x4A);
        uint16_t rows = static_cast<uint16_t>(real_readb(0x40, 0x84) + 1);
        if (rows < 2) rows = 25;
        const uint16_t start = real_readw(0x40, 0x4E);
        const uint8_t page = real_readb(0x40, 0x62);
        const uint16_t seg = mode == 7 ? 0xB000 : 0xB800;
        JsonValue lines = JsonValue::Array();
        for (uint16_t r = 0; r < rows; ++r) {
            std::string line;
            for (uint16_t c = 0; c < cols; ++c) {
                const uint8_t ch = real_readb(seg, static_cast<uint16_t>(start + 2 * (r * cols + c)));
                line += kCp437[ch];
            }
            while (!line.empty() && line.back() == ' ')
                line.pop_back();
            lines.array.push_back(Str(line));
        }
        Set(result, "zeilen", lines);
        Set(result, "spalten", Num(cols));
        JsonValue cursor = JsonValue::Object();
        Set(&cursor, "spalte", Num(real_readb(0x40, static_cast<uint16_t>(0x50 + page * 2))));
        Set(&cursor, "zeile", Num(real_readb(0x40, static_cast<uint16_t>(0x51 + page * 2))));
        Set(result, "cursor", cursor);
        Set(result, "modus", Hex(mode, 2));
        return true;
    });
    Register("screen.video_state", [](const JsonValue&, JsonValue* result, Error*) {
        Set(result, "modus", Hex(CurMode != nullptr ? CurMode->mode : real_readb(0x40, 0x49), 2));
        Set(result, "typ", Str(IsTextMode() ? "text" : "grafik"));
        Set(result, "breite", Num(CurMode != nullptr ? CurMode->swidth : 0));
        Set(result, "hoehe", Num(CurMode != nullptr ? CurMode->sheight : 0));
        Set(result, "bpp", Num(render.src.bpp));
        Set(result, "quelle_breite", Num(render.src.width));
        Set(result, "quelle_hoehe", Num(render.src.height));
        return true;
    });
}

} // namespace dmcp

void DMCP_FrameDone()
{
    dmcp::ExecOnFrame(); // emu.frame_step counts every finished frame, also when no copy is kept
    using dmcp::g_frame;
    if (scalerSourceCacheBuffer == nullptr || render.src.width == 0 || render.src.height == 0)
        return;
    const size_t bytes = static_cast<size_t>(render.scale.cachePitch) * render.src.height;
    if (bytes == 0 || bytes > scalerSourceCacheBufferSize)
        return;
    g_frame.width = static_cast<unsigned>(render.src.width);
    g_frame.height = static_cast<unsigned>(render.src.height);
    g_frame.bpp = static_cast<unsigned>(render.src.bpp);
    g_frame.pitch = static_cast<unsigned>(render.scale.cachePitch);
    g_frame.text_mode = CurMode != nullptr && CurMode->type == M_TEXT;
    g_frame.game_width = CurMode != nullptr ? static_cast<unsigned>(CurMode->swidth) : 0;
    g_frame.game_height = CurMode != nullptr ? static_cast<unsigned>(CurMode->sheight) : 0;
    g_frame.pixels.assign(scalerSourceCacheBuffer, scalerSourceCacheBuffer + bytes);
    for (int i = 0; i < 256; ++i) {
        g_frame.palette[i][0] = render.pal.rgb[i].red;
        g_frame.palette[i][1] = render.pal.rgb[i].green;
        g_frame.palette[i][2] = render.pal.rgb[i].blue;
    }
    g_frame.valid = true;
}

#endif // C_DMCP
