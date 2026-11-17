# Linux development build: libwebrtc's hermetic libc++ needs clang >= 21.
export CC=/opt/llvm21/bin/clang CXX=/opt/llvm21/bin/clang++ AR=/opt/llvm21/bin/llvm-ar
