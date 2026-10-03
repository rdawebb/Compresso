project('compresso', 'c',
  meson_version: '>=1.3.0',
  default_options: [
    # gnu11 on GCC/Clang, c11 on MSVC; strict ISO defines __STRICT_ANSI__,
    # which hides the POSIX declarations the sources rely on
    'c_std=gnu11,c11',
    'warning_level=2',
  ],
)

py = import('python').find_installation(pure: false)
cc = meson.get_compiler('c')

# bzip2 and snappy do not reliably ship a .pc file (Homebrew snappy has only a
# CMake config), so each falls back to a plain library search
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
  dependency('libzip'),
  dependency('libarchive'),
  bz2_dep,
  snappy_dep,
]

subdir('src/compresso')
