#include "files.h"
#include "unity.h"
#include <stdio.h>

int files_equal(const char *a, const char *b) {
  FILE *fa = fopen(a, "rb");
  FILE *fb = fopen(b, "rb");
  if (!fa || !fb) {
    if (fa) {
      fclose(fa);
    }
    if (fb) {
      fclose(fb);
    }
    return 0;
  }

  int equal = 1;
  for (;;) {
    int ca = fgetc(fa);
    int cb = fgetc(fb);
    if (ca != cb) {
      equal = 0;
      break;
    }
    if (ca == EOF) {
      break;
    }
  }

  fclose(fa);
  fclose(fb);
  return equal;
}

void write_file(const char *path, const char *contents) {
  FILE *f = fopen(path, "wb");
  TEST_ASSERT_NOT_NULL(f);
  if (contents[0] != '\0') {
    fputs(contents, f);
  }
  fclose(f);
}

long file_size(const char *path) {
  FILE *f = fopen(path, "rb");
  if (!f) {
    return -1;
  }
  fseek(f, 0, SEEK_END);
  long size = ftell(f);
  fclose(f);
  return size;
}
