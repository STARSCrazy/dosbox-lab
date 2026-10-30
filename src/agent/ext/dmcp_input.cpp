// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "agent/dmcp.h"
#include "keyboard.h"
#include "pic.h"
#include "mouse.h"
#include "render.h"
#include "mem.h"
#include "../ints/int10.h"
#include "dmcp_internal.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <deque>
#include <map>
#include <set>
#include <vector>

void Mouse_CursorMoved(float xrel, float yrel, float x, float y, bool emulate);
void Mouse_ButtonPressed(uint8_t button);
void Mouse_ButtonReleased(uint8_t button);
void Mouse_WheelMoved(int32_t scroll);
int KEYBOARD_AUX_Active(void);
void DMCP_MouseRead(float* x, float* y, int* min_x, int* max_x, int* min_y, int* max_y, uint8_t* buttons,
                    float* ppm_x, float* ppm_y, float* senv_x, float* senv_y);
extern bool user_cursor_locked;
extern MOUSE_EMULATION user_cursor_emulation;

namespace dmcp {
namespace {

struct KeyEvent {
    double due_ms;      // PIC_FullIndex() time
    KBD_KEYS key;
    bool pressed;
};

std::deque<KeyEvent> g_queue;
std::set<int> g_held;
double g_next_free_ms = 0.0;
bool g_input_owned = true;

const std::map<std::string, KBD_KEYS>& KeyNames()
{
    static std::map<std::string, KBD_KEYS> names;
    if (!names.empty())
        return names;
    const char* letters = "QWERTYUIOPASDFGHJKLZXCVBNM";
    const KBD_KEYS letter_keys[] = {KBD_q, KBD_w, KBD_e, KBD_r, KBD_t, KBD_y, KBD_u, KBD_i, KBD_o, KBD_p,
                                    KBD_a, KBD_s, KBD_d, KBD_f, KBD_g, KBD_h, KBD_j, KBD_k, KBD_l,
                                    KBD_z, KBD_x, KBD_c, KBD_v, KBD_b, KBD_n, KBD_m};
    for (int i = 0; i < 26; ++i)
        names[std::string(1, letters[i])] = letter_keys[i];
    const KBD_KEYS digits[] = {KBD_0, KBD_1, KBD_2, KBD_3, KBD_4, KBD_5, KBD_6, KBD_7, KBD_8, KBD_9};
    for (int i = 0; i < 10; ++i)
        names[std::string(1, static_cast<char>('0' + i))] = digits[i];
    const KBD_KEYS fkeys[] = {KBD_f1, KBD_f2, KBD_f3, KBD_f4, KBD_f5, KBD_f6,
                              KBD_f7, KBD_f8, KBD_f9, KBD_f10, KBD_f11, KBD_f12};
    for (int i = 0; i < 12; ++i)
        names["F" + std::to_string(i + 1)] = fkeys[i];
    const KBD_KEYS keypad[] = {KBD_kp0, KBD_kp1, KBD_kp2, KBD_kp3, KBD_kp4, KBD_kp5, KBD_kp6, KBD_kp7, KBD_kp8, KBD_kp9};
    for (int i = 0; i < 10; ++i)
        names["KP" + std::to_string(i)] = keypad[i];
    const std::pair<const char*, KBD_KEYS> rest[] = {
        {"ESC", KBD_esc}, {"TAB", KBD_tab}, {"BACKSPACE", KBD_backspace}, {"ENTER", KBD_enter},
        {"SPACE", KBD_space}, {"ALT", KBD_leftalt}, {"ALTGR", KBD_rightalt}, {"CTRL", KBD_leftctrl},
        {"RCTRL", KBD_rightctrl}, {"SHIFT", KBD_leftshift}, {"RSHIFT", KBD_rightshift},
        {"CAPSLOCK", KBD_capslock}, {"SCROLLLOCK", KBD_scrolllock}, {"NUMLOCK", KBD_numlock},
        {"GRAVE", KBD_grave}, {"MINUS", KBD_minus}, {"EQUALS", KBD_equals}, {"BACKSLASH", KBD_backslash},
        {"LBRACKET", KBD_leftbracket}, {"RBRACKET", KBD_rightbracket}, {"SEMICOLON", KBD_semicolon},
        {"QUOTE", KBD_quote}, {"PERIOD", KBD_period}, {"COMMA", KBD_comma}, {"SLASH", KBD_slash},
        {"LTGT", KBD_extra_lt_gt}, {"PRINT", KBD_printscreen}, {"PAUSE", KBD_pause},
        {"INS", KBD_insert}, {"HOME", KBD_home}, {"PGUP", KBD_pageup}, {"DEL", KBD_delete},
        {"END", KBD_end}, {"PGDN", KBD_pagedown}, {"LEFT", KBD_left}, {"UP", KBD_up},
        {"DOWN", KBD_down}, {"RIGHT", KBD_right}, {"KPDIVIDE", KBD_kpdivide},
        {"KPMULTIPLY", KBD_kpmultiply}, {"KPMINUS", KBD_kpminus}, {"KPPLUS", KBD_kpplus},
        {"KPENTER", KBD_kpenter}, {"KPPERIOD", KBD_kpperiod}};
    for (const auto& entry : rest)
        names[entry.first] = entry.second;
    return names;
}

struct Stroke {
    KBD_KEYS key;
    bool shift;
    bool altgr;
};

// Characters per layout (code point -> key + modifiers). Layout "de" assumes the guest uses "keyb gr".
const std::map<uint32_t, Stroke>& LayoutMap(const std::string& layout)
{
    static std::map<uint32_t, Stroke> us, de;
    std::map<uint32_t, Stroke>& map = layout == "de" ? de : us;
    if (!map.empty())
        return map;
    const auto& names = KeyNames();
    for (char c = 'a'; c <= 'z'; ++c) {
        const KBD_KEYS key = names.at(std::string(1, static_cast<char>(std::toupper(c))));
        map[static_cast<uint32_t>(c)] = {key, false, false};
        map[static_cast<uint32_t>(std::toupper(c))] = {key, true, false};
    }
    for (char c = '0'; c <= '9'; ++c)
        map[static_cast<uint32_t>(c)] = {names.at(std::string(1, c)), false, false};
    map[' '] = {KBD_space, false, false};
    map['\n'] = {KBD_enter, false, false};
    map['\r'] = {KBD_enter, false, false};
    map['\t'] = {KBD_tab, false, false};
    if (layout == "de") {
        map['y'] = {KBD_z, false, false};  map['Y'] = {KBD_z, true, false};
        map['z'] = {KBD_y, false, false};  map['Z'] = {KBD_y, true, false};
        const std::pair<uint32_t, Stroke> extra[] = {
            {'!', {KBD_1, true, false}}, {'"', {KBD_2, true, false}}, {0xA7, {KBD_3, true, false}},
            {'$', {KBD_4, true, false}}, {'%', {KBD_5, true, false}}, {'&', {KBD_6, true, false}},
            {'/', {KBD_7, true, false}}, {'(', {KBD_8, true, false}}, {')', {KBD_9, true, false}},
            {'=', {KBD_0, true, false}}, {0xDF, {KBD_minus, false, false}}, {'?', {KBD_minus, true, false}},
            {0xB4, {KBD_equals, false, false}}, {'`', {KBD_equals, true, false}},
            {0xFC, {KBD_leftbracket, false, false}}, {0xDC, {KBD_leftbracket, true, false}},
            {'+', {KBD_rightbracket, false, false}}, {'*', {KBD_rightbracket, true, false}},
            {'~', {KBD_rightbracket, false, true}}, {0xF6, {KBD_semicolon, false, false}},
            {0xD6, {KBD_semicolon, true, false}}, {0xE4, {KBD_quote, false, false}},
            {0xC4, {KBD_quote, true, false}}, {'#', {KBD_backslash, false, false}},
            {'\'', {KBD_backslash, true, false}}, {'<', {KBD_extra_lt_gt, false, false}},
            {'>', {KBD_extra_lt_gt, true, false}}, {'|', {KBD_extra_lt_gt, false, true}},
            {',', {KBD_comma, false, false}}, {';', {KBD_comma, true, false}},
            {'.', {KBD_period, false, false}}, {':', {KBD_period, true, false}},
            {'-', {KBD_slash, false, false}}, {'_', {KBD_slash, true, false}},
            {'^', {KBD_grave, false, false}}, {0xB0, {KBD_grave, true, false}},
            {'@', {KBD_q, false, true}}, {'\\', {KBD_minus, false, true}}, {'{', {KBD_7, false, true}},
            {'[', {KBD_8, false, true}}, {']', {KBD_9, false, true}}, {'}', {KBD_0, false, true}},
            {0x20AC, {KBD_e, false, true}}};
        for (const auto& entry : extra)
            map[entry.first] = entry.second;
    } else {
        const std::pair<uint32_t, Stroke> extra[] = {
            {'-', {KBD_minus, false, false}}, {'_', {KBD_minus, true, false}},
            {'=', {KBD_equals, false, false}}, {'+', {KBD_equals, true, false}},
            {'[', {KBD_leftbracket, false, false}}, {'{', {KBD_leftbracket, true, false}},
            {']', {KBD_rightbracket, false, false}}, {'}', {KBD_rightbracket, true, false}},
            {'\\', {KBD_backslash, false, false}}, {'|', {KBD_backslash, true, false}},
            {';', {KBD_semicolon, false, false}}, {':', {KBD_semicolon, true, false}},
            {'\'', {KBD_quote, false, false}}, {'"', {KBD_quote, true, false}},
            {'`', {KBD_grave, false, false}}, {'~', {KBD_grave, true, false}},
            {',', {KBD_comma, false, false}}, {'<', {KBD_comma, true, false}},
            {'.', {KBD_period, false, false}}, {'>', {KBD_period, true, false}},
            {'/', {KBD_slash, false, false}}, {'?', {KBD_slash, true, false}},
            {'!', {KBD_1, true, false}}, {'@', {KBD_2, true, false}}, {'#', {KBD_3, true, false}},
            {'$', {KBD_4, true, false}}, {'%', {KBD_5, true, false}}, {'^', {KBD_6, true, false}},
            {'&', {KBD_7, true, false}}, {'*', {KBD_8, true, false}}, {'(', {KBD_9, true, false}},
            {')', {KBD_0, true, false}}};
        for (const auto& entry : extra)
            map[entry.first] = entry.second;
    }
    return map;
}

bool DecodeUtf8(const std::string& text, std::vector<uint32_t>* out)
{
    for (size_t i = 0; i < text.size();) {
        const unsigned char c = static_cast<unsigned char>(text[i]);
        uint32_t cp = 0;
        size_t len = 0;
        if (c < 0x80) { cp = c; len = 1; }
        else if ((c & 0xE0) == 0xC0) { cp = c & 0x1F; len = 2; }
        else if ((c & 0xF0) == 0xE0) { cp = c & 0x0F; len = 3; }
        else if ((c & 0xF8) == 0xF0) { cp = c & 0x07; len = 4; }
        else return false;
        if (i + len > text.size())
            return false;
        for (size_t k = 1; k < len; ++k)
            cp = (cp << 6) | (static_cast<unsigned char>(text[i + k]) & 0x3F);
        out->push_back(cp);
        i += len;
    }
    return true;
}

std::string EncodeUtf8(uint32_t cp)
{
    std::string s;
    if (cp < 0x80) s.push_back(static_cast<char>(cp));
    else if (cp < 0x800) { s.push_back(static_cast<char>(0xC0 | (cp >> 6))); s.push_back(static_cast<char>(0x80 | (cp & 0x3F))); }
    else if (cp < 0x10000) { s.push_back(static_cast<char>(0xE0 | (cp >> 12))); s.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F))); s.push_back(static_cast<char>(0x80 | (cp & 0x3F))); }
    else { s.push_back(static_cast<char>(0xF0 | (cp >> 18))); s.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F))); s.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F))); s.push_back(static_cast<char>(0x80 | (cp & 0x3F))); }
    return s;
}

void Enqueue(KBD_KEYS key, bool pressed, double delay_ms)
{
    const double now = PIC_FullIndex();
    if (g_next_free_ms < now)
        g_next_free_ms = now;
    g_next_free_ms += delay_ms;
    g_queue.push_back({g_next_free_ms, key, pressed});
}

// Presses modifiers, the key, waits hold_ms, releases in reverse order.
void EnqueueCombo(const std::vector<KBD_KEYS>& modifiers, KBD_KEYS key, double hold_ms, double gap_ms)
{
    for (KBD_KEYS mod : modifiers)
        Enqueue(mod, true, 5.0);
    Enqueue(key, true, 5.0);
    Enqueue(key, false, hold_ms);
    for (auto it = modifiers.rbegin(); it != modifiers.rend(); ++it)
        Enqueue(*it, false, 5.0);
    g_next_free_ms += gap_ms;
}

// Drops the queue and releases every held key (input.release_all, input.own(false)).
void ReleaseAll()
{
    g_queue.clear();
    for (int key : g_held)
        KEYBOARD_AddKey(static_cast<KBD_KEYS>(key), false);
    g_held.clear();
    g_next_free_ms = 0.0;
}

JsonValue PendingResult()
{
    JsonValue result = JsonValue::Object();
    Set(&result, "ausstehend", Num(g_queue.size()));
    return result;
}

struct MouseView {
    float x, y;
    int min_x, max_x, min_y, max_y;
    uint8_t buttons;
    float ppm_x, ppm_y, senv_x, senv_y;
};

MouseView ReadMouse()
{
    MouseView v;
    DMCP_MouseRead(&v.x, &v.y, &v.min_x, &v.max_x, &v.min_y, &v.max_y, &v.buttons,
                   &v.ppm_x, &v.ppm_y, &v.senv_x, &v.senv_y);
    return v;
}

// Relative motion as if the user had captured the mouse, without touching the host cursor.
void InjectMotion(float xrel, float yrel)
{
    const bool saved_locked = user_cursor_locked;
    const MOUSE_EMULATION saved_emulation = user_cursor_emulation;
    user_cursor_locked = true;                          // INT 33h mickeys and PS/2 motion need "locked"
    user_cursor_emulation = MOUSE_EMULATION_ALWAYS;     // keep position relative, ignore host cursor
    Mouse_CursorMoved(xrel, yrel, 0.0f, 0.0f, true);
    user_cursor_locked = saved_locked;
    user_cursor_emulation = saved_emulation;
}

// INT 33h pixels per injected motion unit (same rule for absolute and relative moves).
double MotionDivisor(float ppm, float senv)
{
    return static_cast<double>(ppm) * (senv > 0 ? senv : 1.0f);
}

float ToMotion(double pixels, double divisor)
{
    return static_cast<float>(divisor > 0 ? pixels / divisor : pixels);
}

JsonValue MouseStateJson()
{
    const MouseView v = ReadMouse();
    JsonValue result = JsonValue::Object();
    JsonValue pos = JsonValue::Object();
    Set(&pos, "x", NumF(v.x));
    Set(&pos, "y", NumF(v.y));
    Set(&result, "int33", pos);
    Set(&result, "knoepfe", Num(v.buttons));
    JsonValue range = JsonValue::Object();
    Set(&range, "min_x", NumF(v.min_x));
    Set(&range, "max_x", NumF(v.max_x));
    Set(&range, "min_y", NumF(v.min_y));
    Set(&range, "max_y", NumF(v.max_y));
    Set(&result, "bereich", range);
    Set(&result, "ps2", Bool(KEYBOARD_AUX_Active() != 0));
    return result;
}

bool ParseSignedParam(const JsonValue& params, const std::string& name, double* out)
{
    const JsonValue* value = Param(params, name);
    if (value == nullptr || value->type != JsonType::Number)
        return false;
    char* end = nullptr;
    *out = std::strtod(value->text.c_str(), &end);
    return end != value->text.c_str() && *end == '\0';
}

int ButtonIndex(const std::string& name)
{
    if (name == "links") return 0;
    if (name == "rechts") return 1;
    if (name == "mitte") return 2;
    return -1;
}

} // namespace

void InputTick()
{
    if (g_queue.empty())
        return;
    const double now = PIC_FullIndex();
    while (!g_queue.empty() && g_queue.front().due_ms <= now) {
        const KeyEvent event = g_queue.front();
        g_queue.pop_front();
        KEYBOARD_AddKey(event.key, event.pressed);
        if (event.pressed)
            g_held.insert(static_cast<int>(event.key));
        else
            g_held.erase(static_cast<int>(event.key));
    }
}

void RegisterInput()
{
    Register("input.key", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string spec;
        if (!ParamStr(params, "taste", &spec) || spec.empty())
            return Fail(error, "bad_argument", "parameter taste is required, e.g. \"ENTER\" or \"ALT+X\"");
        uint64_t hold = 60;
        if (Param(params, "dauer_ms") != nullptr && (!ParamU64(params, "dauer_ms", &hold) || hold > 10000))
            return Fail(error, "bad_argument", "dauer_ms must be 0..10000");
        std::string upper = spec;
        std::transform(upper.begin(), upper.end(), upper.begin(), [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
        std::vector<KBD_KEYS> keys;
        size_t start = 0;
        while (start <= upper.size()) {
            const size_t plus = upper.find('+', start);
            const std::string part = upper.substr(start, plus == std::string::npos ? std::string::npos : plus - start);
            const auto found = KeyNames().find(part);
            if (found == KeyNames().end())
                return Fail(error, "bad_argument", "unknown key name \"" + part + "\"");
            keys.push_back(found->second);
            if (plus == std::string::npos)
                break;
            start = plus + 1;
        }
        const KBD_KEYS main_key = keys.back();
        keys.pop_back();
        EnqueueCombo(keys, main_key, static_cast<double>(hold), 20.0);
        *result = PendingResult();
        return true;
    });
    Register("input.type", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string text;
        std::string layout = "de";
        uint64_t gap = 40;
        if (!ParamStr(params, "text", &text))
            return Fail(error, "bad_argument", "parameter text is required");
        ParamStr(params, "layout", &layout);
        if (layout != "de" && layout != "us")
            return Fail(error, "bad_argument", "layout must be de or us");
        if (Param(params, "abstand_ms") != nullptr && (!ParamU64(params, "abstand_ms", &gap) || gap > 5000))
            return Fail(error, "bad_argument", "abstand_ms must be 0..5000");
        std::vector<uint32_t> cps;
        if (!DecodeUtf8(text, &cps))
            return Fail(error, "bad_argument", "text is not valid UTF-8");
        const auto& map = LayoutMap(layout);
        std::vector<Stroke> strokes;
        for (size_t i = 0; i < cps.size(); ++i) {
            const uint32_t cp = cps[i];
            if (cp == '\r' && i + 1 < cps.size() && cps[i + 1] == '\n')
                continue; // CRLF is one Enter
            const auto found = map.find(cp);
            if (found == map.end())
                return Fail(error, "bad_argument", "character \"" + EncodeUtf8(cp) + "\" cannot be typed with layout " + layout);
            strokes.push_back(found->second);
        }
        for (const Stroke& s : strokes) {
            std::vector<KBD_KEYS> mods;
            if (s.shift) mods.push_back(KBD_leftshift);
            if (s.altgr) mods.push_back(KBD_rightalt);
            EnqueueCombo(mods, s.key, 30.0, static_cast<double>(gap));
        }
        *result = PendingResult();
        Set(result, "zeichen", Num(strokes.size()));
        return true;
    });
    Register("input.pending", [](const JsonValue&, JsonValue* result, Error*) {
        *result = PendingResult();
        return true;
    });
    Register("input.release_all", [](const JsonValue&, JsonValue* result, Error*) {
        ReleaseAll();
        *result = PendingResult();
        return true;
    });
    Register("input.mouse_move", [](const JsonValue& params, JsonValue* result, Error* error) {
        double x = 0, y = 0;
        std::string coords = "spiel";
        if (!ParseSignedParam(params, "x", &x) || !ParseSignedParam(params, "y", &y))
            return Fail(error, "bad_argument", "x and y (numbers) are required");
        ParamStr(params, "koordinaten", &coords);
        MouseView v = ReadMouse();
        double tx = x, ty = y;
        if (coords == "spiel") {
            // Game resolution as the program sees it (mode 13h: 320x200). render.src.height would be 400
            // there (VGA scan doubling), so prefer the INT 10h mode geometry.
            double gw = static_cast<double>(render.src.width), gh = static_cast<double>(render.src.height);
            if (CurMode != nullptr && CurMode->swidth != 0 && CurMode->sheight != 0) {
                gw = static_cast<double>(CurMode->swidth);
                gh = static_cast<double>(CurMode->sheight);
            }
            if (gw <= 0 || gh <= 0)
                return Fail(error, "no_frame", "no video mode yet, cannot convert game pixels");
            tx = v.min_x + x * (v.max_x - v.min_x + 1) / gw;
            ty = v.min_y + y * (v.max_y - v.min_y + 1) / gh;
        } else if (coords != "int33") {
            return Fail(error, "bad_argument", "koordinaten must be spiel or int33");
        }
        // The driver clamps its position to the range; aiming beyond it would only pile up mickeys.
        const double cx = (std::min)((std::max)(tx, static_cast<double>(v.min_x)), static_cast<double>(v.max_x));
        const double cy = (std::min)((std::max)(ty, static_cast<double>(v.min_y)), static_cast<double>(v.max_y));
        const bool clamped = cx != tx || cy != ty;
        tx = cx;
        ty = cy;
        const bool ps2 = KEYBOARD_AUX_Active() != 0;
        bool reached = false;
        bool have_last = false;
        float last_x = 0, last_y = 0;
        for (int i = 0; i < 64 && !ps2; ++i) {
            v = ReadMouse();
            const double ex = tx - v.x, ey = ty - v.y;
            if (std::fabs(ex) < 0.5 && std::fabs(ey) < 0.5) {
                reached = true;
                break;
            }
            if (have_last && v.x == last_x && v.y == last_y)
                break; // driver position no longer moves (e.g. range changed): stop instead of flooding mickeys
            have_last = true;
            last_x = v.x;
            last_y = v.y;
            InjectMotion(ToMotion(ex, MotionDivisor(v.ppm_x, v.senv_x)),
                         ToMotion(ey, MotionDivisor(v.ppm_y, v.senv_y)));
        }
        if (ps2) // PS/2 mouse in the guest: no INT 33h feedback, move once open-loop
            InjectMotion(static_cast<float>(tx - v.x), static_cast<float>(ty - v.y));
        *result = MouseStateJson();
        JsonValue target = JsonValue::Object();
        Set(&target, "x", NumF(tx));
        Set(&target, "y", NumF(ty));
        Set(result, "ziel", target);
        Set(result, "erreicht", Bool(reached));
        if (clamped)
            Set(result, "geklemmt", Bool(true));
        return true;
    });
    Register("input.mouse_move_rel", [](const JsonValue& params, JsonValue* result, Error* error) {
        double dx = 0, dy = 0;
        if (!ParseSignedParam(params, "dx", &dx) || !ParseSignedParam(params, "dy", &dy))
            return Fail(error, "bad_argument", "dx and dy (numbers) are required");
        const MouseView v = ReadMouse();
        InjectMotion(ToMotion(dx, MotionDivisor(v.ppm_x, v.senv_x)), ToMotion(dy, MotionDivisor(v.ppm_y, v.senv_y)));
        *result = MouseStateJson();
        return true;
    });
    Register("input.mouse_button", [](const JsonValue& params, JsonValue* result, Error* error) {
        std::string name;
        bool pressed = false;
        if (!ParamStr(params, "knopf", &name) || ButtonIndex(name) < 0 || !ParamBool(params, "gedrueckt", &pressed))
            return Fail(error, "bad_argument", "knopf (links|rechts|mitte) and gedrueckt (bool) are required");
        const uint8_t button = static_cast<uint8_t>(ButtonIndex(name));
        if (pressed)
            Mouse_ButtonPressed(button);
        else
            Mouse_ButtonReleased(button);
        *result = MouseStateJson();
        return true;
    });
    Register("input.mouse_wheel", [](const JsonValue& params, JsonValue* result, Error* error) {
        double steps = 0;
        if (!ParseSignedParam(params, "schritte", &steps))
            return Fail(error, "bad_argument", "schritte (number) is required");
        Mouse_WheelMoved(static_cast<int32_t>(steps));
        *result = MouseStateJson();
        return true;
    });
    Register("input.mouse_state", [](const JsonValue&, JsonValue* result, Error*) {
        *result = MouseStateJson();
        return true;
    });
    Register("input.own", [](const JsonValue& params, JsonValue* result, Error* error) {
        bool on = true;
        if (!ParamBool(params, "an", &on))
            return Fail(error, "bad_argument", "parameter an (bool) is required");
        g_input_owned = on;
        if (!on)
            ReleaseAll(); // handing over: no queued presses or held keys may linger
        Set(result, "an", Bool(g_input_owned));
        return true;
    });
}

bool InputOwned() { return g_input_owned; }

} // namespace dmcp

bool DMCP_InputOwned() { return dmcp::InputOwned(); }

#endif // C_DMCP
