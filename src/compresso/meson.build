fsutil_platform = host_machine.system() == 'windows' ? 'fsutil_win32.c' : 'fsutil_posix.c'

py.extension_module(
  '_core',
  files(
    'csrc/_core.c',
    'csrc/backends.c',
    'csrc/common.c',
    'csrc/comp_format.c',
    'csrc/formats.c',
    'csrc/fsutil.c',
    'csrc/pipeline.c',
    'csrc/registry.c',
    'csrc/standalone.c',
    'csrc/validate.c',
    'csrc' / fsutil_platform,
    # Shared codec core
    'csrc/codec/bzip2.c',
    'csrc/codec/codec.c',
    'csrc/codec/crc32c.c',
    'csrc/codec/lz4.c',
    'csrc/codec/lzma.c',
    'csrc/codec/snappy.c',
    'csrc/codec/zlib.c',
    'csrc/codec/zstd.c',
    # Archives
    'csrc/archives/archive_ops.c',
    'csrc/archives/changes.c',
    'csrc/archives/extract.c',
    'csrc/archives/paths.c',
    'csrc/archives/select.c',
    'csrc/archives/tar.c',
    'csrc/archives/zip.c',
    'csrc/archives/walk.c',
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
  'cli/_run.py',
  'cli/algos.py',
  'cli/benchmark.py',
  'cli/compress.py',
  'cli/extract.py',
  'cli/inspect.py',
  subdir: 'compresso/cli',
)

py.install_sources(
  'cli/_render/__init__.py',
  'cli/_render/info.py',
  'cli/_render/jobs.py',
  subdir: 'compresso/cli/_render',
)

py.install_sources(
  'frontend/__init__.py',
  'frontend/_formats.py',
  'frontend/_job.py',
  'frontend/api.py',
  'frontend/archive_api.py',
  'frontend/dispatch.py',
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
