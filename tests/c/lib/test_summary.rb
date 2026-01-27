# Compresso - C Test Suite Summary

require 'fileutils'
require_relative 'colour_output'

class TestSummary
  attr_reader :report, :total_tests, :failures, :ignored
  attr_writer :targets, :root

  def initialize
    @report = ''
    @total_tests = 0
    @failures = 0
    @ignored = 0
    @targets = []
    @root = nil
  end

  def run
    return if @targets.empty?

    # Parse test output lines
    failure_lines = []
    ignore_lines = []

    @targets.each do |test_name, output|
      lines = output.split("\n")
      next if lines.empty?

      # Parse test results from output
      lines.each do |line|
        case line
        when /FAIL/
          failure_lines << "  #{test_name}: #{line}"
        when /IGNORE/
          ignore_lines << "  #{test_name}: #{line}"
        end
      end

      # Count tests, failures, ignores
      lines.each do |line|
        if line =~ /^(\d+) Tests (\d+) Failures (\d+) Ignored/
          @total_tests += Regexp.last_match(1).to_i
          @failures += Regexp.last_match(2).to_i
          @ignored += Regexp.last_match(3).to_i
        end
      end
    end

    # Build report
    @report = "\n"

    if @ignored > 0
      @report += ColourOutput.colorize("---------------------------\n", :cyan)
      @report += ColourOutput.colorize("IGNORED TEST SUMMARY\n", :yellow)
      @report += ColourOutput.colorize("---------------------------\n", :cyan)
      @report += ignore_lines.join("\n") + "\n"
    end

    if @failures > 0
      @report += ColourOutput.colorize("---------------------------\n", :cyan)
      @report += ColourOutput.colorize("FAILED TEST SUMMARY\n", :red)
      @report += ColourOutput.colorize("---------------------------\n", :cyan)
      @report += failure_lines.join("\n") + "\n"
    end

    @report += ColourOutput.colorize("---------------------------\n", :cyan)
    @report += ColourOutput.colorize("OVERALL TEST SUMMARY\n", :blue)
    @report += ColourOutput.colorize("---------------------------\n", :cyan)

    if @failures > 0
      @report += ColourOutput.colorize(
        "#{@total_tests} TOTAL TESTS | #{@failures} FAILURES | #{@ignored} IGNORED\n",
        :red
      )
    else
      @report += ColourOutput.colorize(
        "#{@total_tests} TOTAL TESTS | #{@failures} FAILURES | #{@ignored} IGNORED\n",
        :green
      )
    end
    @report += "\n"

    @report
  end

  def print_report
    puts @report
  end
end
