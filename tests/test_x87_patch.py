"""Compile allocator regressions and execute native ARM rounding emissions.

Requires macOS, clang++, and the pinned upstream git object in
X87SIDECAR_SOURCE (defaults to the prepared release source). All patching and
compiler outputs stay in a temporary archive checkout; prepared files are unused.
Run with: python3 -m unittest discover -s tests -p test_x87_patch.py -v
Set X87SIDECAR_BUILD_PROOF=1 to also CMake-build a pristine patched sidecar
and run the standalone translator matrix. This never starts Wine.
"""

import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
REVISION = '4e9c7386d9253e838692c05015de9ae61bd6a11f'
PATCH = ROOT / 'patches/x87sidecar/0001-honor-pc24-arithmetic.patch'

HARNESS = r'''
#include <algorithm>
#include <bit>
#include <cassert>
#include <cstdio>
#include <vector>
#include <pthread.h>
#include <sys/mman.h>
#include <libkern/OSCacheControl.h>

#include "rosetta_core/Register.h"
#include "rosetta_core/ThreadContextOffsets.h"
#include "rosetta_core/TranslationResult.h"
#include "rosetta_core/X87IR.h"
#include "rosetta_core/X87Precision.hpp"
#include "rosetta_core/AssemblerHelpers.hpp"
#include "rosetta_core/TranslatorX87.h"
#include "rosetta_core/IRInstr.h"
#include "rosetta_core/Opcode.h"

// The standalone emitter uses the identity opcode mapping, without a loader.
uint64_t rosetta_core_runtime_version() { return 0x16f0200000000ULL; }

// Delegate allocation to upstream, observing its real mask high-water mark.
int observed_peak;
uint32_t initial_mask;
int x87_test_alloc_free_fpr(TranslationResult&);
int alloc_free_fpr(TranslationResult& result) {
    int reg = x87_test_alloc_free_fpr(result);
    observed_peak = std::max(observed_peak,
                            std::popcount(initial_mask & ~result.free_fpr_mask));
    return reg;
}

void check(X87IR::Context ctx, int expected, int arithmetic_node) {
    using namespace X87IR;
    for (int budget = 0; budget <= 8; ++budget) {
        int over = -2;
        assert(peak_live_fprs(ctx, budget, &over) == expected);
        // These cases start with one or two distinct constants, then arithmetic.
        int expected_over = budget >= expected ? -1 :
                            budget < arithmetic_node ? budget : arithmetic_node;
        assert(over == expected_over);
    }
    assert(peak_live_fprs(ctx, 8, nullptr) == expected);

    // Clamp the actual allocator, not just the estimator's admission budget.
    initial_mask = 0;
    uint32_t available = kFprScratchMask;
    for (int i = 0; i < expected; ++i) {
        uint32_t bit = available & -available;
        assert(bit);
        initial_mask |= bit;
        available &= ~bit;
    }
    ThreadContextOffsets offsets{};
    std::vector<uint32_t> code(16384);
    TranslationResult result{};
    result.insn_buf.data = code.data();
    result.insn_buf.end_cap = code.size() * sizeof(uint32_t);
    result.free_gpr_mask = kGprScratchMask;
    result.free_fpr_mask = initial_mask;
    result.thread_context_offsets = &offsets;
    result.x87_cache.run_remaining = 1;
    observed_peak = 0;
    lower(ctx, &result);
    assert(result.insn_buf.end > 0);
    assert(observed_peak == expected);
    assert(result.free_fpr_mask == initial_mask);
}

void numeric() {
    // AAPCS wrapper saves the translator's callee-saved scratch registers.
    // Its state capture is emitted adjacent to the helper, before C can change flags.
    void* memory = mmap(nullptr, 16384, PROT_READ | PROT_WRITE | PROT_EXEC,
                        MAP_PRIVATE | MAP_ANON | MAP_JIT, -1, 0);
    assert(memory != MAP_FAILED);
    int cases = 0;
    for (bool store : {false, true}) {
        pthread_jit_write_protect_np(0);
        TranslationResult result{};
        auto& b = result.insn_buf;
        b.data = static_cast<uint32_t*>(memory);
        b.end_cap = 16384;
        result.free_gpr_mask = (1u << 22) | (1u << 23) | (1u << 24);
        result.free_fpr_mask = kFprScratchMask;
        emit_add_imm(b, 1, 1, 0, 0, 32, 31, 31);
        for (int i = 0; i < 3; ++i)
            emit_ldr_str_imm(b, 3, 0, 0, i, 31, 22 + i);
        for (int i = 0; i < 3; ++i) {
            b.emit((i == 0 ? 0xd53b4400 : i == 1 ? 0xd53b4420 : 0xd53b4200) | (6 + i));
            emit_ldr_str_imm(b, 3, 0, 1, i, 1, 3);
            b.emit((i == 0 ? 0xd51b4400 : i == 1 ? 0xd51b4420 : 0xd51b4200) | 3);
        }
        emit_movn(b, 1, 2, 0, 0x1234, 2);
        if (store) emit_x87_store_f32(result, b, 0, 0, 0);
        else emit_x87_arithmetic(result, b, 0, 2, X87Arithmetic::Add, 0, 0, 1);
        for (int i = 0; i < 3; ++i) {
            b.emit((i == 0 ? 0xd53b4400 : i == 1 ? 0xd53b4420 : 0xd53b4200) | 3);
            emit_ldr_str_imm(b, 3, 0, 0, i, 1, 3);
            b.emit((i == 0 ? 0xd51b4400 : i == 1 ? 0xd51b4420 : 0xd51b4200) | (6 + i));
        }
        b.emit(0x9e660003); // FMOV X3, D0
        emit_ldr_str_imm(b, 3, 0, 0, 3, 1, 3);
        emit_ldr_str_imm(b, 3, 0, 0, 4, 1, 2);
        for (int i = 0; i < 3; ++i)
            emit_ldr_str_imm(b, 3, 0, 1, i, 31, 22 + i);
        emit_add_imm(b, 1, 0, 0, 0, 32, 31, 31);
        b.emit(0xd65f03c0);
        sys_icache_invalidate(memory, b.end);
        pthread_jit_write_protect_np(1);
        auto call = reinterpret_cast<void(*)(uint16_t*, uint64_t*, double, double)>(memory);
        for (unsigned host = 0; host < 4; ++host)
            for (unsigned rc = 0; rc < 4; ++rc)
                for (unsigned neg = 0; neg < 2; ++neg) {
                    uint16_t cw = (store ? 0x007f : 0x027f) | (rc << 10);
                    uint64_t fpcr = (uint64_t(host) << 22) | (1u << 24) | (1u << 25);
                    uint64_t state[] = {fpcr, 1, 0xa0000000, 0, 0};
                    uint64_t sign = uint64_t(neg) << 63;
                    double lhs = std::bit_cast<double>((store ? 0x3ff0000010000000ULL : 0x3ff0000000000000ULL) | sign);
                    double rhs = std::bit_cast<double>(0x3ca0000000000000ULL | sign);
                    call(&cw, state, lhs, rhs);
                    uint64_t want = store ? ((uint64_t(neg) << 31) | (0x3f800000 + (rc == (neg ? 1u : 2u)))) :
                                           ((0x3ff0000000000000ULL | sign) + (rc == (neg ? 1u : 2u)));
                    assert(state[0] == fpcr && state[1] == 0x11 && state[2] == 0xa0000000);
                    assert(state[3] == want && state[4] == 0x1234);
                    ++cases;
                }
    }
    munmap(memory, 16384);
    std::printf("%d native ARM numeric/FPCR/FPSR/NZCV/borrowed-register cases passed\n", cases);
}

void single_stores() {
    for (bool pop : {false, true}) {
        ThreadContextOffsets offsets{};
        std::vector<uint32_t> code(16384);
        TranslationResult result{};
        result.insn_buf.data = code.data();
        result.insn_buf.end_cap = code.size() * sizeof(uint32_t);
        result.free_gpr_mask = kGprScratchMask;
        result.free_fpr_mask = kFprScratchMask;
        result.thread_context_offsets = &offsets;
        IRInstr instr{};
        instr.operands[0].mem.kind = IROperandKind::MemRef;
        instr.operands[0].mem.size = IROperandSize::S32;
        instr.operands[0].mem.addr_size = IROperandSize::S64;
        instr.operands[0].mem.mem_flags = 1;
        instr.set_opcode(pop ? kOpcodeName_fstp : kOpcodeName_fst);
        assert(TranslatorX87::try_translate_single_fast(&result, &instr));
        assert(result.free_gpr_mask == kGprScratchMask);
        assert(result.free_fpr_mask == kFprScratchMask);
    }
    std::puts("2 isolated FST/FSTP allocator cases passed");
}

int main() {
    numeric();
    single_stores();
    using namespace X87IR;
    int cases = 0;
    for (Op op : {Op::FAdd, Op::FSub, Op::FMul, Op::FDiv, Op::FSqrt}) {
        for (bool same_input : {false, true}) {
            if (op == Op::FSqrt && same_input) continue;
            for (int retained = 0; retained < 4; ++retained) {
                if (op == Op::FSqrt && retained > 1) continue;
                Context ctx{};
                ctx.init();
                int16_t lhs = ctx.add_node(Op::ConstOne);
                int16_t rhs = op == Op::FSqrt ? -1 :
                              same_input ? lhs : ctx.add_node(Op::ConstOne);
                int16_t out = ctx.add_node(op, lhs, rhs);
                ctx.slot_val[0] = out;
                if (retained & 1) ctx.slot_val[1] = lhs;
                if (retained & 2) ctx.slot_val[2] = rhs;
                bool can_reuse = same_input ? retained == 0 :
                                 op == Op::FSqrt ? retained == 0 : retained != 3;
                int expected = out + !can_reuse + (op == Op::FSqrt ? 2 : 3);
                check(ctx, expected, out);
                ++cases;

                // Reuse the output repeatedly. Duplicate dying inputs must not
                // free it twice or lower the live count of subsequent nodes.
                for (int i = 0; i < 3; ++i) {
                    out = ctx.add_node(op, out, op == Op::FSqrt ? -1 : out);
                }
                ctx.slot_val[0] = out;
                check(ctx, expected, op == Op::FSqrt || same_input ? 1 : 2);
                ++cases;
            }
        }
    }
    std::printf("%d native lowerer/pressure cases passed\n", cases);
}
'''


class X87PatchTests(unittest.TestCase):
    def test_native_pressure_matches_lowerer(self):
        source = Path(os.environ.get(
            'X87SIDECAR_SOURCE', ROOT / '.build/release/sources/x87sidecar'))
        compiler = shutil.which('clang++')
        if sys.platform != 'darwin' or platform.machine() != 'arm64' or compiler is None:
            self.skipTest('requires native arm64 macOS and clang++')
        if not (source / '.git').exists():
            self.skipTest('requires the pinned x87sidecar git checkout')

        with tempfile.TemporaryDirectory(prefix='x87-patch-test-') as directory:
            work = Path(directory)
            archive = subprocess.run(
                ['git', '-C', str(source), 'archive', REVISION],
                check=True, capture_output=True).stdout
            subprocess.run(['tar', '-x', '-C', str(work)], input=archive, check=True)
            subprocess.run(['git', 'apply', '--check', '--whitespace=error-all', str(PATCH)],
                           cwd=work, check=True)
            subprocess.run(['git', 'apply', str(PATCH)], cwd=work, check=True)
            harness = work / 'pressure_test.cpp'
            harness.write_text(HARNESS)
            include = work / 'rosetta_core/include'
            sources = work / 'rosetta_core/src'
            flags = [compiler, '-std=c++23', '-O0', '-g', '-UNDEBUG', '-I', str(include)]
            objects = []
            # Only the lowerer and its emitter dependencies. No loader, hooks,
            # Wine, CMake configuration, or prepared build artifacts are used.
            for name in ('X87IRLower', 'X87Precision', 'AssemblerHelpers',
                         'AssemblerBuffer', 'TranslatorHelpers', 'TranslatorX87Helpers',
                         'TranslatorX87Transcendental', 'TranscendentalHelper',
                          'TranslatorX87', 'TranslatorX87Single', 'X87Cache', 'CoreConfig',
                          'IRInstr', 'OpcodeCompatibility'):
                obj = work / f'{name}.o'
                extra = ['-Dalloc_free_fpr=x87_test_alloc_free_fpr'] if name == 'TranslatorHelpers' else []
                subprocess.run([*flags, *extra, '-c', str(sources / f'{name}.cpp'),
                                '-o', str(obj)], check=True)
                objects.append(str(obj))
            executable = work / 'pressure_test'
            subprocess.run([*flags, str(harness), *objects, '-Wl,-dead_strip',
                            '-o', str(executable)], check=True)
            subprocess.run([str(executable)], cwd=work, check=True)
            if os.environ.get('X87SIDECAR_BUILD_PROOF') == '1':
                build = work / 'build'
                subprocess.run(['cmake', '-S', str(work), '-B', str(build),
                                '-DCMAKE_OSX_ARCHITECTURES=arm64'], check=True)
                subprocess.run(['cmake', '--build', str(build), '-j', '8'], check=True)
                env = dict(os.environ, MACTMNF_X87_SRC=str(build), MACTMNF_X87_SOURCE_ONLY='1')
                subprocess.run(['bash', str(ROOT / 'src/shell/tools/probes/smoke-x87sidecar.sh')],
                               env=env, check=True, timeout=300)


if __name__ == '__main__':
    unittest.main()
