# Unity test suite for the C sources
# Links the codec and format code directly rather than through the extension,
# so it needs none of the archive libraries the root build does

project('compresso-c-tests', 'c',
  meson_version: '>=1.3.0',
  default_options: [
    # Matches the root build; see meson.build there
    'c_std=gnu11,c11',
    'warning_level=2',
  ],
)

cc = meson.get_compiler('c')
py = import('python').find_installation()

# Same fallbacks as the root meson.build
bz2_dep = dependency('bzip2', required: false)
if not bz2_dep.found()
  bz2_dep = cc.find_library('bz2', has_headers: 'bzlib.h')
endif

snappy_dep = dependency('snappy', 'Snappy', required: false)
if not snappy_dep.found()
  snappy_dep = cc.find_library('snappy', has_headers: 'snappy-c.h')
endif

deps = [
  dependency('zlib'),
  dependency('liblzma'),
  dependency('libzstd'),
  dependency('liblz4'),
  bz2_dep,
  snappy_dep,
  # The backends raise Python exceptions, so the harness embeds an interpreter
  py.dependency(embed: true),
]

csrc = '../../src/compresso/csrc'
inc = include_directories('support', 'unity', csrc)

unity = static_library('unity', 'unity/unity.c', include_directories: inc)

# _core.c is left out: it reaches the archive API and its libarchive/libzip
# stack; support/test_stubs.c supplies the comp_* exception globals it owns instead
under_test = static_library('under_test',
  'support/test_stubs.c',
  'support/backend_io.c',
  csrc / 'common.c',
  csrc / 'format.c',
  csrc / 'fsutil.c',
  csrc / 'registry.c',
  csrc / 'strategy.c',
  csrc / 'codec/codec.c',
  csrc / 'codec/zstd.c',
  csrc / 'codec/lz4.c',
  csrc / 'codec/lzma.c',
  csrc / 'codec/bzip2.c',
  csrc / 'codec/zlib.c',
  csrc / 'compression/py_zlib.c',
  csrc / 'compression/py_bzip2.c',
  csrc / 'compression/py_lzma.c',
  csrc / 'compression/py_zstd.c',
  csrc / 'compression/py_lz4.c',
  csrc / 'compression/py_snappy.c',
  csrc / 'standalone/gzip.c',
  csrc / 'standalone/bzip2.c',
  csrc / 'standalone/xz.c',
  csrc / 'standalone/zstd.c',
  csrc / 'standalone/lz4.c',
  csrc / 'standalone/registry.c',
  include_directories: inc,
  dependencies: deps,
)

# Tests link these through under_test; listing them again in full would pass
# the bare -l ones to the linker twice
compile_deps = []
foreach dep : deps
  compile_deps += dep.partial_dependency(compile_args: true, includes: true)
endforeach

ruby = find_program('ruby')
generate_runner = files('unity/generate_test_runner.rb')

# Tests run from the build dir so their scratch files land there, and find the
# fixtures by absolute path instead
fixture_dir = '-DFIXTURE_DIR="@0@"'.format(
  meson.current_source_dir() / '..' / 'fixtures'
)

suites = {
  'test_context.c': 'core',
  'test_format_detection.c': 'core',
  'test_pipeline.c': 'core',
  'test_router.c': 'core',
  'test_codec.c': 'codec',
  'test_backends.c': 'compression',
  'test_standalone.c': 'standalone',
}

foreach source, suite : suites
  name = source.replace('.c', '')

  runner = custom_target(name + '_runner',
    input: source,
    output: name + '_Runner.c',
    command: [ruby, generate_runner, '--use_param_tests=1', '@INPUT@', '@OUTPUT@'],
  )

  exe = executable(name, source, runner,
    include_directories: inc,
    c_args: fixture_dir,
    link_with: [under_test, unity],
    dependencies: compile_deps,
  )

  test(name, exe, suite: suite, workdir: meson.current_build_dir())
endforeach
