// SPDX-License-Identifier: GPL-2.0-or-later
#include "config.h"

#if defined(C_DMCP)

#include "dosbox.h"
#include "cpu.h"
#include "regs.h"
#include "dmcp_addr.h"
#include "dmcp_expr.h"

#include <cctype>
#include <cerrno>
#include <cstdlib>

Bitu FillFlags(void);

namespace dmcp {
namespace {

enum Op { kNum, kReg, kMem, kNot, kBitNot, kNeg, kOr, kAnd, kBitOr, kBitXor, kBitAnd, kEq, kNe, kLt, kLe, kGt,
          kGe, kAdd, kSub };

enum Reg { rEAX, rEBX, rECX, rEDX, rESI, rEDI, rEBP, rESP, rEIP, rAX, rBX, rCX, rDX, rSI, rDI, rBP, rSP, rIP,
           rAL, rAH, rBL, rBH, rCL, rCH, rDL, rDH, rCS, rDS, rES, rFS, rGS, rSS, rEFLAGS, rCF, rPF, rAF, rZF,
           rSF, rTF, rIF, rDF, rOF, rCPL, rPMODE };

struct RegName {
    const char* name;
    int id;
};

const RegName kRegs[] = {
    {"eax", rEAX}, {"ebx", rEBX}, {"ecx", rECX}, {"edx", rEDX}, {"esi", rESI}, {"edi", rEDI}, {"ebp", rEBP},
    {"esp", rESP}, {"eip", rEIP}, {"ax", rAX}, {"bx", rBX}, {"cx", rCX}, {"dx", rDX}, {"si", rSI}, {"di", rDI},
    {"bp", rBP}, {"sp", rSP}, {"ip", rIP}, {"al", rAL}, {"ah", rAH}, {"bl", rBL}, {"bh", rBH}, {"cl", rCL},
    {"ch", rCH}, {"dl", rDL}, {"dh", rDH}, {"cs", rCS}, {"ds", rDS}, {"es", rES}, {"fs", rFS}, {"gs", rGS},
    {"ss", rSS}, {"eflags", rEFLAGS}, {"cf", rCF}, {"pf", rPF}, {"af", rAF}, {"zf", rZF}, {"sf", rSF},
    {"tf", rTF}, {"if", rIF}, {"df", rDF}, {"of", rOF}, {"cpl", rCPL}, {"pmode", rPMODE}};

uint64_t Flag(Bitu mask)
{
    FillFlags();
    return (reg_flags & mask) != 0 ? 1 : 0;
}

uint64_t ReadReg(int id)
{
    switch (id) {
    case rEAX: return reg_eax;
    case rEBX: return reg_ebx;
    case rECX: return reg_ecx;
    case rEDX: return reg_edx;
    case rESI: return reg_esi;
    case rEDI: return reg_edi;
    case rEBP: return reg_ebp;
    case rESP: return reg_esp;
    case rEIP: return reg_eip;
    case rAX: return reg_ax;
    case rBX: return reg_bx;
    case rCX: return reg_cx;
    case rDX: return reg_dx;
    case rSI: return reg_si;
    case rDI: return reg_di;
    case rBP: return reg_bp;
    case rSP: return reg_sp;
    case rIP: return reg_ip;
    case rAL: return reg_al;
    case rAH: return reg_ah;
    case rBL: return reg_bl;
    case rBH: return reg_bh;
    case rCL: return reg_cl;
    case rCH: return reg_ch;
    case rDL: return reg_dl;
    case rDH: return reg_dh;
    case rCS: return SegValue(cs);
    case rDS: return SegValue(ds);
    case rES: return SegValue(es);
    case rFS: return SegValue(fs);
    case rGS: return SegValue(gs);
    case rSS: return SegValue(ss);
    case rEFLAGS: FillFlags(); return reg_flags & 0xFFFFFFFFu;
    case rCF: return Flag(FLAG_CF);
    case rPF: return Flag(FLAG_PF);
    case rAF: return Flag(FLAG_AF);
    case rZF: return Flag(FLAG_ZF);
    case rSF: return Flag(FLAG_SF);
    case rTF: return Flag(FLAG_TF);
    case rIF: return Flag(FLAG_IF);
    case rDF: return Flag(FLAG_DF);
    case rOF: return Flag(FLAG_OF);
    case rCPL: return cpu.cpl;
    case rPMODE: return cpu.pmode ? 1 : 0;
    default: return 0;
    }
}

uint64_t ReadMem(uint32_t linear, int width)
{
    uint64_t value = 0;
    for (int i = width - 1; i >= 0; --i) {
        uint8_t byte = 0;
        if (!PeekLinear(linear + static_cast<uint32_t>(i), &byte))
            return 0;
        value = (value << 8) | byte;
    }
    return value;
}

struct OpTok {
    const char* tok;
    int op;
};

// Binary operators per precedence level, lowest first; longer tokens before their prefixes.
const OpTok kL0[] = {{"||", kOr}};
const OpTok kL1[] = {{"&&", kAnd}};
const OpTok kL2[] = {{"|", kBitOr}};
const OpTok kL3[] = {{"^", kBitXor}};
const OpTok kL4[] = {{"&", kBitAnd}};
const OpTok kL5[] = {{"==", kEq}, {"!=", kNe}};
const OpTok kL6[] = {{"<=", kLe}, {">=", kGe}, {"<", kLt}, {">", kGt}};
const OpTok kL7[] = {{"+", kAdd}, {"-", kSub}};

struct Level {
    const OpTok* ops;
    size_t count;
};

const Level kLevels[] = {{kL0, 1}, {kL1, 1}, {kL2, 1}, {kL3, 1}, {kL4, 1}, {kL5, 2}, {kL6, 4}, {kL7, 2}};
const int kLevelCount = 8;

class Parser {
public:
    Parser(const std::string& text, std::vector<Expr::Node>* nodes, Error* error)
        : s_(text), nodes_(nodes), error_(error) {}

    bool Parse(int* root)
    {
        if (!Binary(0, root))
            return false;
        Skip();
        if (pos_ != s_.size())
            return Fail(error_, "bad_argument", "unexpected text in condition at: " + s_.substr(pos_));
        return true;
    }

private:
    void Skip()
    {
        while (pos_ < s_.size() && std::isspace(static_cast<unsigned char>(s_[pos_])))
            ++pos_;
    }

    // Adds a node; -1 (error set) when the tree gets deeper than kMaxDepth (EvalNode recursion stays bounded).
    int Add(int op, uint64_t value, int a, int b)
    {
        int depth = 1;
        if (op != kMem && a >= 0)
            depth = depths_[static_cast<size_t>(a)] + 1;
        if (b >= 0 && depths_[static_cast<size_t>(b)] + 1 > depth)
            depth = depths_[static_cast<size_t>(b)] + 1;
        if (depth > Expr::kMaxDepth) {
            Fail(error_, "bad_argument", "condition is nested too deeply (at most " +
                                             std::to_string(Expr::kMaxDepth) + " levels)");
            return -1;
        }
        Expr::Node node;
        node.op = op;
        node.value = value;
        node.a = a;
        node.b = b;
        nodes_->push_back(node);
        depths_.push_back(depth);
        return static_cast<int>(nodes_->size()) - 1;
    }

    // Parser nesting (parentheses, unary operators): the recursion must not overflow the emulation thread stack.
    bool Enter()
    {
        if (++nesting_ > Expr::kMaxDepth)
            return Fail(error_, "bad_argument", "condition is nested too deeply (at most " +
                                                    std::to_string(Expr::kMaxDepth) + " levels)");
        return true;
    }

    bool MatchOp(int level, int* op)
    {
        Skip();
        const Level& l = kLevels[level];
        for (size_t i = 0; i < l.count; ++i) {
            const std::string tok = l.ops[i].tok;
            if (s_.compare(pos_, tok.size(), tok) != 0)
                continue;
            const char next = pos_ + tok.size() < s_.size() ? s_[pos_ + tok.size()] : '\0';
            // "|" must not eat "||", "&" not "&&", "<"/">" not "<="/">=" (those are listed first anyway).
            if ((tok == "|" && next == '|') || (tok == "&" && next == '&'))
                continue;
            pos_ += tok.size();
            *op = l.ops[i].op;
            return true;
        }
        return false;
    }

    bool Binary(int level, int* out)
    {
        if (level == kLevelCount)
            return Unary(out);
        int lhs = -1;
        if (!Binary(level + 1, &lhs))
            return false;
        int op = 0;
        while (MatchOp(level, &op)) {
            int rhs = -1;
            if (!Binary(level + 1, &rhs))
                return false;
            lhs = Add(op, 0, lhs, rhs);
            if (lhs < 0)
                return false;
        }
        *out = lhs;
        return true;
    }

    bool Unary(int* out)
    {
        Skip();
        if (pos_ >= s_.size())
            return Fail(error_, "bad_argument", "condition ends unexpectedly: " + s_);
        const char c = s_[pos_];
        if ((c == '!' && (pos_ + 1 >= s_.size() || s_[pos_ + 1] != '=')) || c == '~' || c == '-') {
            ++pos_;
            int a = -1;
            if (!Enter() || !Unary(&a))
                return false;
            --nesting_;
            *out = Add(c == '!' ? kNot : c == '~' ? kBitNot : kNeg, 0, a, -1);
            return *out >= 0;
        }
        if (c == '(') {
            ++pos_;
            if (!Enter() || !Binary(0, out))
                return false;
            --nesting_;
            Skip();
            if (pos_ >= s_.size() || s_[pos_] != ')')
                return Fail(error_, "bad_argument", "missing ) in condition: " + s_);
            ++pos_;
            return true;
        }
        if (std::isdigit(static_cast<unsigned char>(c)))
            return Number(out);
        if (std::isalpha(static_cast<unsigned char>(c)))
            return Name(out);
        return Fail(error_, "bad_argument", "unexpected character in condition at: " + s_.substr(pos_));
    }

    bool Number(int* out)
    {
        int base = 10;
        size_t start = pos_;
        if (s_.compare(pos_, 2, "0x") == 0 || s_.compare(pos_, 2, "0X") == 0) {
            base = 16;
            start = pos_ + 2;
        }
        size_t end = start;
        while (end < s_.size() && std::isxdigit(static_cast<unsigned char>(s_[end])) &&
               (base == 16 || std::isdigit(static_cast<unsigned char>(s_[end]))))
            ++end;
        if (end == start)
            return Fail(error_, "bad_argument", "bad number in condition at: " + s_.substr(pos_));
        errno = 0;
        const unsigned long long value = std::strtoull(s_.substr(start, end - start).c_str(), nullptr, base);
        if (errno != 0 || value > 0xFFFFFFFFull)
            return Fail(error_, "bad_argument", "number too large in condition: " + s_.substr(pos_, end - pos_));
        pos_ = end;
        *out = Add(kNum, value, -1, -1);
        return true;
    }

    bool Name(int* out)
    {
        size_t end = pos_;
        while (end < s_.size() && std::isalnum(static_cast<unsigned char>(s_[end])))
            ++end;
        std::string name = s_.substr(pos_, end - pos_);
        for (char& ch : name)
            ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
        pos_ = end;
        int width = name == "byte" ? 1 : name == "word" ? 2 : name == "dword" ? 4 : 0;
        Skip();
        if (width != 0 && pos_ < s_.size() && s_[pos_] == '[') {
            const size_t close = s_.find(']', pos_);
            if (close == std::string::npos)
                return Fail(error_, "bad_argument", "missing ] in condition: " + s_);
            std::string text = s_.substr(pos_ + 1, close - pos_ - 1);
            while (!text.empty() && std::isspace(static_cast<unsigned char>(text.back())))
                text.pop_back();
            while (!text.empty() && std::isspace(static_cast<unsigned char>(text.front())))
                text.erase(text.begin());
            Address address;
            if (!ParseAddress(text, &address, error_)) {
                error_->code = "bad_argument";
                return false;
            }
            uint32_t linear = address.linear;
            if (!address.has_linear) {
                if ((cpu.cr0 & 0x80000000u) != 0)
                    return Fail(error_, "bad_argument", "p: in a condition needs paging off; use a linear address");
                linear = address.phys;
            }
            pos_ = close + 1;
            *out = Add(kMem, linear, width, -1);
            return true;
        }
        for (const RegName& reg : kRegs) {
            if (name == reg.name) {
                *out = Add(kReg, static_cast<uint64_t>(reg.id), -1, -1);
                return true;
            }
        }
        return Fail(error_, "bad_argument", "unknown name in condition: " + name);
    }

    const std::string& s_;
    std::vector<Expr::Node>* nodes_;
    Error* error_;
    size_t pos_ = 0;
    int nesting_ = 0;
    std::vector<int> depths_; // tree depth per node (parallel to nodes_)
};

uint64_t EvalNode(const std::vector<Expr::Node>& n, int i)
{
    const Expr::Node& x = n[static_cast<size_t>(i)];
    const uint64_t mask = 0xFFFFFFFFull;
    switch (x.op) {
    case kNum: return x.value;
    case kReg: return ReadReg(static_cast<int>(x.value));
    case kMem: return ReadMem(static_cast<uint32_t>(x.value), x.a);
    case kNot: return EvalNode(n, x.a) == 0 ? 1 : 0;
    case kBitNot: return ~EvalNode(n, x.a) & mask;
    case kNeg: return (0 - EvalNode(n, x.a)) & mask;
    case kOr: return (EvalNode(n, x.a) != 0 || EvalNode(n, x.b) != 0) ? 1 : 0;
    case kAnd: return (EvalNode(n, x.a) != 0 && EvalNode(n, x.b) != 0) ? 1 : 0;
    case kBitOr: return EvalNode(n, x.a) | EvalNode(n, x.b);
    case kBitXor: return EvalNode(n, x.a) ^ EvalNode(n, x.b);
    case kBitAnd: return EvalNode(n, x.a) & EvalNode(n, x.b);
    case kEq: return EvalNode(n, x.a) == EvalNode(n, x.b) ? 1 : 0;
    case kNe: return EvalNode(n, x.a) != EvalNode(n, x.b) ? 1 : 0;
    case kLt: return EvalNode(n, x.a) < EvalNode(n, x.b) ? 1 : 0;
    case kLe: return EvalNode(n, x.a) <= EvalNode(n, x.b) ? 1 : 0;
    case kGt: return EvalNode(n, x.a) > EvalNode(n, x.b) ? 1 : 0;
    case kGe: return EvalNode(n, x.a) >= EvalNode(n, x.b) ? 1 : 0;
    case kAdd: return (EvalNode(n, x.a) + EvalNode(n, x.b)) & mask;
    case kSub: return (EvalNode(n, x.a) - EvalNode(n, x.b)) & mask;
    default: return 0;
    }
}

} // namespace

bool Expr::Compile(const std::string& text, Error* error)
{
    nodes_.clear();
    root_ = -1;
    text_.clear();
    if (text.find_first_not_of(" \t") == std::string::npos)
        return Fail(error, "bad_argument", "condition is empty");
    if (text.size() > kMaxText)
        return Fail(error, "bad_argument", "condition is too long (at most " + std::to_string(kMaxText) + " bytes)");
    std::vector<Node> nodes;
    Parser parser(text, &nodes, error);
    int root = -1;
    if (!parser.Parse(&root))
        return false;
    nodes_ = nodes;
    root_ = root;
    text_ = text;
    return true;
}

uint64_t Expr::Eval() const
{
    if (nodes_.empty())
        return 0;
    return EvalNode(nodes_, root_);
}

} // namespace dmcp

#endif // C_DMCP
