// libFuzzer target for what `info` reads from a header without decoding

#include "standalone.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

static char path[] = "/tmp/compresso-fuzz-XXXXXX";

static void remove_path(void) { unlink(path); }

int LLVMFuzzerInitialize(int *argc, char ***argv) {
  (void)argc;
  (void)argv;
  int fd = mkstemp(path);
  if (fd < 0) {
    perror("mkstemp");
    abort();
  }
  close(fd);
  atexit(remove_path);
  return 0;
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  FILE *f = fopen(path, "wb");
  if (!f) {
    return 0;
  }
  fwrite(data, 1, size, f);
  fclose(f);
  free(gzip_original_name(path));

  f = fopen(path, "rb");
  if (f) {
    uint64_t content_size;
    standalone_content_size(f, FORMAT_XZ, &content_size);
    standalone_content_size(f, FORMAT_ZSTD, &content_size);
    standalone_content_size(f, FORMAT_LZ4, &content_size);
    standalone_content_size(f, FORMAT_SNAPPY, &content_size);
    fclose(f);
  }
  return 0;
}
