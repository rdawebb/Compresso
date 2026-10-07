// libFuzzer target for magic-byte format detection

#include "archives.h"
#include <stdint.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  detect_format_from_magic_bytes(data, size);
  return 0;
}
