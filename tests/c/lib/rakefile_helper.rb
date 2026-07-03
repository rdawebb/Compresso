# Compresso - C Test Suite Helper

require 'fileutils'
require_relative 'generate_test_runner'
require_relative 'colour_output'

# Configuration
CC = 'gcc'
CFLAGS = [
  '-Wall',
  '-Wextra',
  '-std=c11',
  '-g',
  '-I.',
  '-I../../src/compresso/csrc'
]

# Resolve Python build settings from the *active* interpreter (python3) so the
# headers we compile against and the libpython we link always come from the
# same version. Do NOT use python3-config: on some systems (e.g. a uv/venv
# python3 alongside a Homebrew python3-config) it resolves to a different
# interpreter, producing a header/library version mismatch.
PY_QUERY = "import sysconfig; g=sysconfig.get_config_var; " \
           "print(sysconfig.get_path('include')); " \
           "print(g('LIBPL') or ''); print(g('LIBDIR') or ''); " \
           "print(g('LDVERSION') or g('VERSION') or '')"
py_cfg = `python3 -c "#{PY_QUERY}" 2>/dev/null`.split("\n")
py_include, py_libpl, py_libdir, py_ldversion = py_cfg

CFLAGS << "-I#{py_include}" if py_include && !py_include.empty?

# Linker flags
LDFLAGS = [
  '-lz',
  '-lbz2',
  '-llzma',
  '-lzstd',
  '-llz4',
  '-lsnappy'
]

LDFLAGS << "-L#{py_libpl}" if py_libpl && !py_libpl.empty?
LDFLAGS << "-L#{py_libdir}" if py_libdir && !py_libdir.empty?
LDFLAGS << (py_ldversion && !py_ldversion.empty? ? "-lpython#{py_ldversion}" : '-lpython3')
LDFLAGS << '-ldl'

# BUILD_DIR is set by rakefile before requiring this file
# SRC_DIR is calculated relative to tests/c/ directory
SRC_DIR = File.join(__dir__, '..', '..', '..', 'src', 'compresso', 'csrc')

# Source files needed for linking.
#
# Note: _core.c is intentionally excluded. It references the archive API, which
# pulls in the libarchive/libzip stack and its platform-specific paths. The
# comp_* exception globals it owns are provided by lib/test_stubs.c instead.
SOURCE_FILES = [
  'unity.c',  # Unity framework implementation
  File.join(__dir__, 'test_stubs.c'),
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

# Map a source file to its object-file path. Uses the path relative to SRC_DIR
# (slashes flattened) so that files sharing a basename — e.g. registry.c and
# standalone/registry.c — produce distinct object files instead of colliding.
def object_file_for(source_file)
  rel = source_file.start_with?(SRC_DIR) ? source_file[SRC_DIR.length + 1..] : File.basename(source_file)
  safe = rel.sub(/\.c$/, '').gsub(/[\/\\]/, '_')
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

  # Compile source files if not already compiled
  src_obj_files = SOURCE_FILES.map do |src_file|
    obj_file = object_file_for(src_file)
    unless File.exist?(obj_file)
      compile_file(src_file)
    end
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
  ColourOutput.puts_colored("-" * 50, :cyan)
  system(exe_file) or raise "Test failed: #{File.basename(exe_file)}"
  ColourOutput.puts_colored("-" * 50, :cyan)
end

def run_single_test(test_file)
  test_name = File.basename(test_file, '.c')

  puts "\n" + "=" * 50
  puts "Building and running: #{test_name}"
  puts "=" * 50 + "\n"

  # Generate runner
  runner_file = generate_test_runner(test_file)

  # Compile test file
  test_obj = compile_file(test_file)

  # Compile runner
  runner_obj = compile_file(runner_file)

  # Link
  exe_file = link_test(test_name, [test_obj, runner_obj])

  # Run
  run_test(exe_file)

  puts "\n✓ #{test_name} passed!\n"
end

def run_all_tests
  test_files = find_test_files

  ColourOutput.puts_colored("\n" + "=" * 50, :blue)
  ColourOutput.puts_colored("Running all Compresso C tests", :blue)
  ColourOutput.puts_colored("=" * 50 + "\n", :blue)

  failed_tests = []

  test_files.each do |test_file|
    begin
      run_single_test(test_file)
    rescue => e
      failed_tests << File.basename(test_file, '.c')
      ColourOutput.puts_colored("✗ Test failed: #{File.basename(test_file, '.c')}", :red)
      ColourOutput.puts_colored("  Error: #{e.message}\n\n", :red)
    end
  end

  ColourOutput.puts_colored("\n" + "=" * 50, :blue)
  if failed_tests.empty?
    ColourOutput.puts_colored("All tests passed! ✓", :green)
  else
    ColourOutput.puts_colored("Failed tests: #{failed_tests.join(', ')}", :red)
    raise "Some tests failed"
  end
  ColourOutput.puts_colored("=" * 50 + "\n", :blue)
end

def run_all_compression_tests
  test_files = Dir.glob('compression/test_*.c').sort

  ColourOutput.puts_colored("\n" + "=" * 50, :blue)
  ColourOutput.puts_colored("Running compression backend tests", :blue)
  ColourOutput.puts_colored("=" * 50 + "\n", :blue)

  failed_tests = []

  test_files.each do |test_file|
    begin
      run_single_test(test_file)
    rescue => e
      failed_tests << File.basename(test_file, '.c')
      ColourOutput.puts_colored("✗ Test failed: #{File.basename(test_file, '.c')}", :red)
      ColourOutput.puts_colored("  Error: #{e.message}\n\n", :red)
    end
  end

  ColourOutput.puts_colored("\n" + "=" * 50, :blue)
  if failed_tests.empty?
    ColourOutput.puts_colored("All compression tests passed! ✓", :green)
  else
    ColourOutput.puts_colored("Failed tests: #{failed_tests.join(', ')}", :red)
    raise "Some compression tests failed"
  end
  ColourOutput.puts_colored("=" * 50 + "\n", :blue)
end
