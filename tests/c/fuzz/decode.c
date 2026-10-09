// libFuzzer target for one standalone decoder, chosen by FORMAT

#define PY_SSIZE_T_CLEAN
#include "standalone.h"
#include "test_stubs.h"
#include <Python.h>
#include <stdint.h>
#include <stdio.h>

#ifndef FORMAT
#error "define FORMAT as the container's Format, e.g. -DFORMAT=FORMAT_GZIP"
#endif

static FILE *sink;

int LLVMFuzzerInitialize(int *argc, char ***argv) {
  (void)argc;
  (void)argv;
  Py_Initialize();
  ensure_comp_exceptions();
  // Each TrailingDataWarning names a different offset, so none would repeat
  PyRun_SimpleString("import warnings; warnings.simplefilter('ignore')");
  sink = fopen("/dev/null", "wb");
  return 0;
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  // fmemopen may refuse an empty buffer
  if (size == 0) {
    return 0;
  }
  FILE *src = fmemopen((void *)data, size, "rb");
  if (!src) {
    return 0;
  }
  const FormatDesc *fmt = find_standalone_format(FORMAT);
  codec_run_stream(fmt->engine(), &fmt->dec, 1, src, sink, NULL);
  fclose(src);
  PyErr_Clear();
  return 0;
}
