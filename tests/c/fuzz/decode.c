// libFuzzer target for one standalone decoder, chosen by FUZZ_<FORMAT>

#define PY_SSIZE_T_CLEAN
#include "codec/codec.h"
#include "test_stubs.h"
#include <Python.h>
#include <stdint.h>
#include <stdio.h>

// The params each standalone/*.c decompress_file passes
#if defined(FUZZ_GZIP)
#define OPS codec_zlib_ops
static const CodecParams PARAMS = {.wrap = CODEC_WRAP_GZIP,
                                   .concatenated = 1,
                                   .ignore_trailing = 1,
                                   .label = "gzip"};
#elif defined(FUZZ_BZIP2)
#define OPS codec_bzip2_ops
static const CodecParams PARAMS = {.concatenated = 1, .ignore_trailing = 1};
#elif defined(FUZZ_XZ)
#define OPS codec_lzma_ops
static const CodecParams PARAMS = {.concatenated = 1, .label = "xz"};
#elif defined(FUZZ_ZSTD)
#define OPS codec_zstd_ops
static const CodecParams PARAMS = {.concatenated = 1};
#elif defined(FUZZ_LZ4)
#define OPS codec_lz4_ops
static const CodecParams PARAMS = {.concatenated = 1};
#else
#error "define one of FUZZ_GZIP, FUZZ_BZIP2, FUZZ_XZ, FUZZ_ZSTD, FUZZ_LZ4"
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
  codec_run_stream(OPS(), &PARAMS, 1, src, sink, NULL);
  fclose(src);
  PyErr_Clear();
  return 0;
}
