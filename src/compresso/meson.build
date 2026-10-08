py.extension_module('_core',
  files(
    'csrc/_core.c',
    'csrc/common.c',
    'csrc/compress.c',
    'csrc/pipeline.c',
    'csrc/formats.c',
    'csrc/standalone.c',
    'csrc/registry.c',
    'csrc/strategy.c',
    'csrc/archives.c',
    'csrc/validate.c',
    'csrc/fsutil.c',
    # Shared codec core
    'csrc/codec/codec.c',
    'csrc/codec/zstd.c',
    'csrc/codec/lz4.c',
    'csrc/codec/lzma.c',
    'csrc/codec/bzip2.c',
    'csrc/codec/zlib.c',
    # Compression algorithms
    'csrc/compression/py_zlib.c',
    'csrc/compression/py_bzip2.c',
    'csrc/compression/py_lzma.c',
    'csrc/compression/py_zstd.c',
    'csrc/compression/py_lz4.c',
    'csrc/compression/py_snappy.c',
    # Archive backends
    'csrc/archives/tar.c',
    'csrc/archives/zip.c',
  ),
  dependencies: deps,
  subdir: 'compresso',
  install: true,
)

# Only listed files are installed, and an editable install can import only
# what is listed here, so a new module must be added before it will import
py.install_sources(
  '__init__.py',
  '_levels.py',
  '_core.pyi',
  'py.typed',
  subdir: 'compresso',
)

py.install_sources(
  'cli/__init__.py',
  'cli/__main__.py',
  'cli/_app.py',
  'cli/_dispatch.py',
  'cli/_render.py',
  'cli/algos.py',
  'cli/benchmark.py',
  'cli/compress.py',
  'cli/extract.py',
  'cli/inspect.py',
  subdir: 'compresso/cli',
)

py.install_sources(
  'frontend/__init__.py',
  'frontend/_job.py',
  'frontend/api.py',
  'frontend/archive_api.py',
  subdir: 'compresso/frontend',
)

py.install_sources(
  'introspect/__init__.py',
  'introspect/benchmark.py',
  'introspect/capabilities.py',
  'introspect/file_inspect.py',
  'introspect/speeds.py',
  subdir: 'compresso/introspect',
)
