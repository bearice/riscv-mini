// Compile-only check for freestanding C++ and the RV32IM libgcc multilib.
extern "C" unsigned mini_cpp_smoke(unsigned value) {
    unsigned long long product = static_cast<unsigned long long>(value) * 1000003ULL;
    return static_cast<unsigned>(product / 97ULL);
}
