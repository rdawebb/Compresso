// libFuzzer target for magic-byte format detection

#include "formats.h"
#include <stdint.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  format_by_magic(data, size);
  return 0;
}
