/*
 * shim.h — one line, on purpose. The C ABI is the real API and lypning.h is
 * its single source: a copy here would be a second header that could drift
 * from the one `lypning build --lib` installs, and a Swift binding that agreed
 * with its own copy and not with the library would fail at link time at best
 * and silently at worst. So this file only points at the original, three
 * directories up in the same asset tree that ships in the wheel.
 */
#include "../../../include/lypning.h"
