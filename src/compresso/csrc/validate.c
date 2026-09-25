#define PY_SSIZE_T_CLEAN
#include "validate.h"
#include "standalone.h"
#include <Python.h>

// -1 selects each backend's default, and is accepted by all of them
#define LEVEL_DEFAULT (-1)

int validate_level(const char *name, LevelRange levels, int level) {
  if (level == LEVEL_DEFAULT)
    return 0;

  if (level_range_is_empty(levels)) {
    PyErr_Format(PyExc_ValueError,
                 "%s has no compression levels (only -1, the default)", name);
    return -1;
  }

  if (level < levels.min || level > levels.max) {
    PyErr_Format(PyExc_ValueError,
                 "%s compression level %d out of range (%d-%d, or -1 for "
                 "default)",
                 name, level, levels.min, levels.max);
    return -1;
  }

  return 0;
}

// A pipeline's level belongs to its codec stage if it has one, otherwise to
// the container itself (zip's deflate; tar has none)
static int validate_pipeline_level(const CompressionPipeline *pipeline,
                                   int level) {
  if (pipeline->codec != FORMAT_UNKNOWN) {
    const StandaloneFormat *fmt = find_standalone_format(pipeline->codec);
    return fmt ? validate_level(fmt->name, fmt->levels, level) : 0;
  }

  // A container without a backend (e.g. 7z) is refused later, by name
  const CArchive *archive = find_archive_by_id(pipeline->archive);
  return archive ? validate_level(archive->name, archive->levels, level) : 0;
}

int validate_compression_request(AlgoID algo, Strategy strategy, int level,
                                 const CompressionPipeline *pipeline) {
  if (pipeline) {
    if (!pipeline_is_valid(pipeline)) {
      PyErr_SetString(PyExc_ValueError,
                      "Unsupported archive/codec combination");
      return -1;
    }
    return validate_pipeline_level(pipeline, level);
  }

  // The backend compress_file will use; a missing one is reported there
  const CBackend *backend =
      algo != ALGO_NONE ? find_backend_by_id(algo) : choose_backend(strategy);
  return backend ? validate_level(backend->name, backend->levels, level) : 0;
}

int validate_overwrite_arg(int overwrite_existing) {
  if (overwrite_existing < 0 || overwrite_existing > 3) {
    PyErr_Format(PyExc_ValueError,
                 "overwrite must be 0 (error), 1 (skip), 2 (overwrite) or "
                 "3 (rename), not %d",
                 overwrite_existing);
    return -1;
  }
  return 0;
}
