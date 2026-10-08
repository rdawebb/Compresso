// libFuzzer target for the gzip header's stored name, which is read by path

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
  return 0;
}
