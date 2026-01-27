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

# Try to get Python includes - try multiple methods
python_include = `python3-config --includes 2>/dev/null`.chomp
if python_include.empty?
  # Fallback: try to get Python path
  python_path = `python3 -c "import sysconfig; print(sysconfig.get_path('include'))" 2>/dev/null`.chomp
  python_include = "-I#{python_path}" unless python_path.empty?
end
CFLAGS << python_include unless python_include.empty?

# Linker flags
LDFLAGS = [
  '-lz',
  '-lbz2',
  '-llzma',
  '-lzstd',
  '-llz4',
  '-lsnappy'
]

# Try to get Python ldflags - this includes both lib dir and libs
python_ldflags = `python3-config --ldflags 2>/dev/null`.chomp
if !python_ldflags.empty?
  # Split the string and add as individual flags
  python_ldflags.split.each { |flag| LDFLAGS << flag }
end

# Always also add the specific Python library
python_version = `python3 --version 2>/dev/null`.chomp
if python_version.include?('3.14')
  LDFLAGS << '-lpython3.14'
elsif python_version.include?('3.13')
  LDFLAGS << '-lpython3.13'
elsif python_version.include?('3.12')
  LDFLAGS << '-lpython3.12'
elsif python_version.include?('3.11')
  LDFLAGS << '-lpython3.11'
else
  # Fallback
  LDFLAGS << '-lpython3'
end

# BUILD_DIR is set by rakefile before requiring this file
# SRC_DIR is calculated relative to tests/c/ directory
SRC_DIR = File.join(__dir__, '..', '..', '..', 'src', 'compresso', 'csrc')

# Source files needed for linking
SOURCE_FILES = [
  'unity.c',  # Unity framework implementation
  File.join(SRC_DIR, 'format_detection.c'),
  File.join(SRC_DIR, 'router.c'),
  File.join(SRC_DIR, '_core.c'),
  File.join(SRC_DIR, 'compression', 'py_zlib.c'),
  File.join(SRC_DIR, 'compression', 'py_bzip2.c'),
  File.join(SRC_DIR, 'compression', 'py_lzma.c'),
  File.join(SRC_DIR, 'compression', 'py_zstd.c'),
  File.join(SRC_DIR, 'compression', 'py_lz4.c'),
  File.join(SRC_DIR, 'compression', 'py_snappy.c')
]

def find_test_files
  files = Dir.glob('test_*.c') + Dir.glob('compression/test_*.c')
  files.sort
end

def compile_file(source_file)
  obj_file = File.join(BUILD_DIR, File.basename(source_file, '.c') + '.o')

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
    obj_file = File.join(BUILD_DIR, File.basename(src_file, '.c') + '.o')
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
