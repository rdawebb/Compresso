# Compresso - C Test Suite Helper

require 'fileutils'
require_relative 'generate_test_runner'
require_relative 'colour_output'

# `cc` is the platform's default C compiler: gcc on Linux, clang on macOS
CC = ENV.fetch('CC', 'cc')
CFLAGS = [
  '-Wall',
  '-Wextra',
  # gnu11 matches setup.py; strict ISO defines __STRICT_ANSI__, under which
  # glibc withholds POSIX declarations these sources expect (e.g. strdup)
  '-std=gnu11',
  '-g',
  '-I.',
  '-I../../src/compresso/csrc'
]

# clang's default search path covers Homebrew's Intel prefix (/usr/local) but
# not Apple Silicon's (/opt/homebrew), so checks brew for the active prefix rather
# than assuming either one; deliberately macOS-only: the Linux runners may carry a
# Linuxbrew install, and preferring its prefix risks compiling and linking against
# different copies
BREW_PREFIX = RUBY_PLATFORM.include?('darwin') ? `brew --prefix 2>/dev/null`.strip : ''

CFLAGS << "-I#{BREW_PREFIX}/include" unless BREW_PREFIX.empty?

# Resolve Python build settings from the active python3 so headers and
# libpython come from one version; python3-config can resolve to a different
# interpreter (e.g. a uv venv python3 beside a Homebrew python3-config)
PY_QUERY = 'import sysconfig; g=sysconfig.get_config_var; ' \
           "print(sysconfig.get_path('include')); " \
           "print(g('LIBPL') or ''); print(g('LIBDIR') or ''); " \
           "print(g('LDVERSION') or g('VERSION') or '')"
py_cfg = `python3 -c "#{PY_QUERY}" 2>/dev/null`.split("\n")
py_include, py_libpl, py_libdir, py_ldversion = py_cfg

CFLAGS << "-I#{py_include}" if py_include && !py_include.empty?

LDFLAGS = [
  '-lz',
  '-lbz2',
  '-llzma',
  '-lzstd',
  '-llz4',
  '-lsnappy'
]

LDFLAGS << "-L#{BREW_PREFIX}/lib" unless BREW_PREFIX.empty?
LDFLAGS << "-L#{py_libpl}" if py_libpl && !py_libpl.empty?
LDFLAGS << "-L#{py_libdir}" if py_libdir && !py_libdir.empty?
LDFLAGS << (py_ldversion && !py_ldversion.empty? ? "-lpython#{py_ldversion}" : '-lpython3')
LDFLAGS << '-ldl'

# BUILD_DIR is set by rakefile before requiring this file
SRC_DIR = File.join(__dir__, '..', '..', '..', 'src', 'compresso', 'csrc')

# _core.c is excluded: it reaches the archive API, pulling in the
# libarchive/libzip stack and its platform-specific paths; test_stubs.c
# supplies the comp_* exception globals it owns instead
SOURCE_FILES = [
  'unity.c',
  File.join(__dir__, 'test_stubs.c'),
  File.join(SRC_DIR, 'common.c'),
  File.join(SRC_DIR, 'format.c'),
  File.join(SRC_DIR, 'registry.c'),
  File.join(SRC_DIR, 'strategy.c'),
  File.join(SRC_DIR, 'compression', 'py_zlib.c'),
  File.join(SRC_DIR, 'compression', 'py_bzip2.c'),
  File.join(SRC_DIR, 'compression', 'py_lzma.c'),
  File.join(SRC_DIR, 'compression', 'py_zstd.c'),
  File.join(SRC_DIR, 'compression', 'py_lz4.c'),
  File.join(SRC_DIR, 'compression', 'py_snappy.c'),
  File.join(SRC_DIR, 'standalone', 'gzip.c'),
  File.join(SRC_DIR, 'standalone', 'bzip2.c'),
  File.join(SRC_DIR, 'standalone', 'xz.c'),
  File.join(SRC_DIR, 'standalone', 'zstd.c'),
  File.join(SRC_DIR, 'standalone', 'lz4.c'),
  File.join(SRC_DIR, 'standalone', 'registry.c')
]

# Flattens the path relative to SRC_DIR so files sharing a basename — e.g.
# registry.c and standalone/registry.c — get distinct object files
def object_file_for(source_file)
  rel = source_file.start_with?(SRC_DIR) ? source_file[SRC_DIR.length + 1..] : File.basename(source_file)
  safe = rel.sub(/\.c$/, '').gsub(%r{[/\\]}, '_')
  File.join(BUILD_DIR, safe + '.o')
end

def find_test_files
  files = Dir.glob('test_*.c') + Dir.glob('compression/test_*.c') +
          Dir.glob('standalone/test_*.c')
  files.sort
end

def compile_file(source_file)
  obj_file = object_file_for(source_file)

  cmd = "#{CC} #{CFLAGS.join(' ')} -c #{source_file} -o #{obj_file}"
  puts "Compiling: #{source_file}"
  system(cmd) or raise "Compilation failed for #{source_file}"

  obj_file
end

def generate_test_runner(test_file)
  runner_file = File.join(BUILD_DIR, File.basename(test_file, '.c') + '_Runner.c')

  puts "Generating test runner for #{File.basename(test_file)}"
  generator = UnityTestRunnerGenerator.new
  generator.run(test_file, runner_file)

  runner_file
end

def link_test(test_name, obj_files)
  exe_file = File.join(BUILD_DIR, test_name)

  src_obj_files = SOURCE_FILES.map do |src_file|
    obj_file = object_file_for(src_file)
    compile_file(src_file) unless File.exist?(obj_file)
    obj_file
  end

  all_objs = obj_files + src_obj_files
  obj_string = all_objs.join(' ')

  cmd = "#{CC} #{obj_string} #{LDFLAGS.join(' ')} -o #{exe_file}"
  puts "Linking: #{test_name}"
  system(cmd) or raise "Linking failed for #{test_name}"

  exe_file
end

def run_test(exe_file)
  ColourOutput.puts_colored("Running: #{File.basename(exe_file)}", :cyan)
  ColourOutput.puts_colored('-' * 50, :cyan)
  system(exe_file) or raise "Test failed: #{File.basename(exe_file)}"
  ColourOutput.puts_colored('-' * 50, :cyan)
end

def run_single_test(test_file)
  test_name = File.basename(test_file, '.c')

  puts "\n" + '=' * 50
  puts "Building and running: #{test_name}"
  puts '=' * 50 + "\n"

  runner_file = generate_test_runner(test_file)
  test_obj = compile_file(test_file)
  runner_obj = compile_file(runner_file)
  exe_file = link_test(test_name, [test_obj, runner_obj])
  run_test(exe_file)

  puts "\n✓ #{test_name} passed!\n"
end

def run_all_tests
  test_files = find_test_files

  ColourOutput.puts_colored("\n" + '=' * 50, :blue)
  ColourOutput.puts_colored('Running all Compresso C tests', :blue)
  ColourOutput.puts_colored('=' * 50 + "\n", :blue)

  failed_tests = []

  test_files.each do |test_file|
    run_single_test(test_file)
  rescue StandardError => e
    failed_tests << File.basename(test_file, '.c')
    ColourOutput.puts_colored("✗ Test failed: #{File.basename(test_file, '.c')}", :red)
    ColourOutput.puts_colored("  Error: #{e.message}\n\n", :red)
  end

  ColourOutput.puts_colored("\n" + '=' * 50, :blue)
  if failed_tests.empty?
    ColourOutput.puts_colored('All tests passed! ✓', :green)
  else
    ColourOutput.puts_colored("Failed tests: #{failed_tests.join(', ')}", :red)
    raise 'Some tests failed'
  end
  ColourOutput.puts_colored('=' * 50 + "\n", :blue)
end

def run_all_compression_tests
  test_files = Dir.glob('compression/test_*.c').sort

  ColourOutput.puts_colored("\n" + '=' * 50, :blue)
  ColourOutput.puts_colored('Running compression backend tests', :blue)
  ColourOutput.puts_colored('=' * 50 + "\n", :blue)

  failed_tests = []

  test_files.each do |test_file|
    run_single_test(test_file)
  rescue StandardError => e
    failed_tests << File.basename(test_file, '.c')
    ColourOutput.puts_colored("✗ Test failed: #{File.basename(test_file, '.c')}", :red)
    ColourOutput.puts_colored("  Error: #{e.message}\n\n", :red)
  end

  ColourOutput.puts_colored("\n" + '=' * 50, :blue)
  if failed_tests.empty?
    ColourOutput.puts_colored('All compression tests passed! ✓', :green)
  else
    ColourOutput.puts_colored("Failed tests: #{failed_tests.join(', ')}", :red)
    raise 'Some compression tests failed'
  end
  ColourOutput.puts_colored('=' * 50 + "\n", :blue)
end
