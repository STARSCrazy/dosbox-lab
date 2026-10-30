// SPDX-License-Identifier: GPL-2.0-or-later
#ifndef DOSBOX_DMCP_EXPR_H
#define DOSBOX_DMCP_EXPR_H

#include "dmcp_internal.h"

#include <cstdint>
#include <string>
#include <vector>

namespace dmcp {

// Compiled condition over registers, flags and memory, e.g. "ax==5 && byte[l:01000004]>3".
// Numbers: 0x.. hex, otherwise decimal. Names (case-insensitive): eax ebx ecx edx esi edi ebp esp eip,
// ax bx cx dx si di bp sp ip, al ah bl bh cl ch dl dh, cs ds es fs gs ss, eflags, cf pf af zf sf tf if df of,
// cpl, pmode (as emu.status). Memory: byte[ADR] word[ADR] dword[ADR], ADR in agent address syntax, resolved
// once at compile time to a linear address (p: only while paging is off). Operators with C precedence:
// || && | ^ & == != < <= > >= + - and unary ! ~ -, parentheses. Values are 32-bit unsigned.
class Expr {
public:
    struct Node {
        int op = 0;
        uint64_t value = 0;
        int a = -1;
        int b = -1;
    };
    // Limits against stack overflow on the emulation thread: text length, parser nesting and tree depth.
    static const size_t kMaxText = 4096;
    static const int kMaxDepth = 64;
    bool Compile(const std::string& text, Error* error);
    bool Empty() const { return nodes_.empty(); }
    const std::string& Text() const { return text_; }
    // Evaluates on the current CPU state. Unreadable memory reads as 0.
    uint64_t Eval() const;

private:
    std::string text_;
    std::vector<Node> nodes_;
    int root_ = -1;
};

} // namespace dmcp

#endif
